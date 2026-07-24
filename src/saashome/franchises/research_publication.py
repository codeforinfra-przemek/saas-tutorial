"""Publish only explicitly approved Workbench decisions onto directory fields."""

from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation

from django.core.serializers.json import DjangoJSONEncoder
from django.core.validators import URLValidator
from django.db import models, transaction

from .models import (
    Franchise,
    FranchiseResearchEditorialDecision,
    FranchiseResearchFinalization,
    FranchiseResearchPublishedField,
    FranchiseResearchReviewField,
)
from .research_fields import FIELD_PROFILE_MAP, field_metadata


class ResearchPublicationError(ValueError):
    """Raised before an unsafe or ambiguous value reaches the public profile."""


ACCEPTED_DECISIONS = {
    FranchiseResearchReviewField.DECISION_ACCEPTED,
    FranchiseResearchReviewField.DECISION_ACCEPTED_EDITED,
    FranchiseResearchReviewField.DECISION_POLICY_ACCEPTED,
}
MONETARY_ATTRIBUTES = {
    "min_investment",
    "max_investment",
    "initial_fee",
    "liquid_capital_required",
    "net_worth_required",
    "mature_unit_revenue_annual",
    "mature_unit_operating_profit_annual",
}
TARGET_PRECEDENCE = {name: position for position, name in enumerate(FIELD_PROFILE_MAP)}


def _json_value(value):
    return json.loads(json.dumps(value, cls=DjangoJSONEncoder))


def _normalized_value(decision: FranchiseResearchEditorialDecision):
    if decision.value_origin not in {
        FranchiseResearchEditorialDecision.ORIGIN_AI,
        FranchiseResearchEditorialDecision.ORIGIN_POLICY,
    }:
        return None
    if decision.research_field_id is None:
        return None
    values = list(decision.research_field.values.all())
    return values[0] if len(values) == 1 else None


def _plain_number(value: str) -> Decimal:
    cleaned = value.strip().replace("\u00a0", " ").upper()
    if any(marker in cleaned for marker in ("USD", "$", "DOLAR", "EUR", "€")):
        raise ResearchPublicationError("unsupported_currency")
    cleaned = cleaned.replace("PLN", "").replace("ZŁ", "").replace(" ", "")
    cleaned = cleaned.replace(",", ".")
    if not re.fullmatch(r"-?\d+(?:\.\d+)?", cleaned):
        raise ResearchPublicationError("invalid_numeric_value")
    try:
        return Decimal(cleaned)
    except InvalidOperation as exc:
        raise ResearchPublicationError("invalid_numeric_value") from exc


def _typed_value(decision, franchise_attribute: str):
    model_field = Franchise._meta.get_field(franchise_attribute)
    normalized = _normalized_value(decision)
    raw = decision.effective_value.strip()
    try:
        if isinstance(model_field, models.BooleanField):
            if normalized is not None and normalized.boolean_value is not None:
                value = normalized.boolean_value
            else:
                lookup = raw.casefold()
                if lookup in {"true", "tak", "yes", "1"}:
                    value = True
                elif lookup in {"false", "nie", "no", "0"}:
                    value = False
                else:
                    raise ResearchPublicationError("invalid_boolean_value")
        elif isinstance(model_field, models.DecimalField):
            if normalized is not None:
                currency = (normalized.currency or "").strip().upper()
                if (
                    franchise_attribute in MONETARY_ATTRIBUTES
                    and currency not in {"", "PLN"}
                ):
                    raise ResearchPublicationError("unsupported_currency")
                numeric = (
                    normalized.number_max_text
                    if decision.target_field == "investment.total_high"
                    else normalized.number_min_text
                )
                numeric = numeric or normalized.number_max_text
                value = Decimal(str(numeric)) if numeric is not None else _plain_number(raw)
            else:
                value = _plain_number(raw)
        elif isinstance(model_field, models.IntegerField):
            if normalized is not None:
                numeric = normalized.number_min_text or normalized.number_max_text
                number = Decimal(str(numeric)) if numeric is not None else _plain_number(raw)
            else:
                number = _plain_number(raw)
            if number != number.to_integral_value():
                raise ResearchPublicationError("non_integral_value")
            value = int(number)
        else:
            value = raw
            if isinstance(model_field, models.URLField):
                URLValidator()(value)
        value = model_field.clean(value, None)
    except ResearchPublicationError:
        raise
    except Exception as exc:
        raise ResearchPublicationError("invalid_profile_value") from exc
    return value


def _virtual_attribute(target_field: str) -> str:
    return f"research.{target_field}"


def _is_model_attribute(attribute: str) -> bool:
    try:
        Franchise._meta.get_field(attribute)
    except Exception:
        return False
    return True


def _selected_decisions(finalization: FranchiseResearchFinalization):
    """Give every accepted field one public destination.

    Dedicated model columns remain useful to cards and filters. Every other L1
    value (including a second value competing for one legacy column) receives
    an audited virtual destination rendered by the research panel.
    """

    decisions = (
        finalization.field_decisions.filter(decision__in=ACCEPTED_DECISIONS)
        .select_related("research_field")
        .prefetch_related("research_field__values")
    )
    ordered = sorted(
        decisions,
        key=lambda item: (
            TARGET_PRECEDENCE.get(item.target_field, 10_000),
            item.target_field,
            item.pk,
        ),
    )
    used_model_attributes = set()
    selected = []
    for decision in ordered:
        attribute = field_metadata(decision.target_field).franchise_attribute
        if attribute and attribute not in used_model_attributes:
            used_model_attributes.add(attribute)
            selected.append((attribute, decision, True))
        else:
            selected.append((_virtual_attribute(decision.target_field), decision, False))
    return selected


def _existing_projection_actions(finalization):
    return [
        {
            "target_field": item.target_field,
            "franchise_attribute": item.franchise_attribute,
            "status": item.status,
            "issue_code": item.issue_code,
            "previous_value": item.previous_value,
            "projected_value": item.projected_value,
        }
        for item in finalization.published_fields.order_by("franchise_attribute")
    ]


@transaction.atomic
def project_approved_research(
    finalization: FranchiseResearchFinalization,
    *,
    dry_run: bool = False,
):
    """Project accepted decisions and preserve non-research/manual profile values."""

    if finalization.published_fields.exists():
        return _existing_projection_actions(finalization)

    franchise = Franchise.objects.select_for_update().get(
        pk=finalization.workspace.franchise_id
    )
    selected = _selected_decisions(finalization)
    old_publications = list(
        FranchiseResearchPublishedField.objects.select_for_update().filter(
            franchise=franchise,
            is_current=True,
            status=FranchiseResearchPublishedField.STATUS_PROJECTED,
        )
    )
    old_by_attribute = {item.franchise_attribute: item for item in old_publications}
    actions = []
    prepared = {}
    for attribute, decision, is_model_attribute in selected:
        if not is_model_attribute:
            actions.append(
                {
                    "target_field": decision.target_field,
                    "franchise_attribute": attribute,
                    "status": FranchiseResearchPublishedField.STATUS_PROJECTED,
                    "issue_code": "",
                    "previous_value": None,
                    "projected_value": decision.effective_value.strip(),
                    "decision": decision,
                }
            )
            continue
        try:
            value = _typed_value(decision, attribute)
        except ResearchPublicationError as exc:
            # A reviewed value is still useful in the complete L1 panel even
            # when it cannot safely populate a typed legacy model column.
            virtual_attribute = _virtual_attribute(decision.target_field)
            actions.append(
                {
                    "target_field": decision.target_field,
                    "franchise_attribute": virtual_attribute,
                    "status": FranchiseResearchPublishedField.STATUS_PROJECTED,
                    "issue_code": f"legacy_projection:{exc}",
                    "previous_value": None,
                    "projected_value": decision.effective_value.strip(),
                    "decision": decision,
                }
            )
            continue
        old = old_by_attribute.get(attribute)
        current_value = _json_value(getattr(franchise, attribute))
        previous_value = (
            old.previous_value
            if old is not None and current_value == old.projected_value
            else current_value
        )
        projected_value = _json_value(value)
        prepared[attribute] = value
        actions.append(
            {
                "target_field": decision.target_field,
                "franchise_attribute": attribute,
                "status": FranchiseResearchPublishedField.STATUS_PROJECTED,
                "issue_code": "",
                "previous_value": previous_value,
                "projected_value": projected_value,
                "decision": decision,
            }
        )

    selected_attributes = set(prepared)
    for old in old_publications:
        if old.franchise_attribute in selected_attributes:
            continue
        if not _is_model_attribute(old.franchise_attribute):
            continue
        current_value = _json_value(getattr(franchise, old.franchise_attribute))
        if current_value == old.projected_value:
            field = Franchise._meta.get_field(old.franchise_attribute)
            prepared[old.franchise_attribute] = field.to_python(old.previous_value)

    public_actions = [
        {key: value for key, value in action.items() if key != "decision"}
        for action in actions
    ]
    if dry_run:
        return public_actions

    FranchiseResearchPublishedField.objects.filter(
        franchise=franchise,
        is_current=True,
    ).update(is_current=False)
    for attribute, value in prepared.items():
        setattr(franchise, attribute, value)
    if "website_url" in prepared:
        franchise.website_url_status = Franchise.WEBSITE_VALIDATED
    franchise.data_status = (
        Franchise.DATA_STATUS_RESEARCH_REVIEWED
        if finalization.decision == "approved"
        else Franchise.DATA_STATUS_RESEARCH_WITH_GAPS
    )
    franchise.is_verified = bool(
        finalization.decision == "approved"
        and finalization.research_import.checker_passed
        and finalization.research_import.scope_complete
    )
    franchise.save(
        update_fields=sorted(
            set(prepared)
            | {"data_status", "is_verified", "website_url_status", "updated_at"}
        )
    )
    for action in actions:
        FranchiseResearchPublishedField.objects.create(
            franchise=franchise,
            finalization=finalization,
            editorial_decision=action["decision"],
            target_field=action["target_field"],
            franchise_attribute=action["franchise_attribute"],
            value_origin=action["decision"].value_origin,
            effective_value=action["decision"].effective_value,
            previous_value=action["previous_value"],
            projected_value=action["projected_value"],
            status=action["status"],
            issue_code=action["issue_code"],
            is_current=(
                action["status"]
                == FranchiseResearchPublishedField.STATUS_PROJECTED
            ),
        )
    return public_actions
