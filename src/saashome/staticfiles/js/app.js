window.initFranchiseMap = function initFranchiseMap(options) {
    if (window.matchMedia("(max-width: 767px)").matches) {
        window.franchiseMapController = null;
        return;
    }

    const mapId = options && options.mapId ? options.mapId : "franchise-map";
    const markerScriptId = options && options.markerScriptId ? options.markerScriptId : "franchise-map-markers";
    const mapElement = document.getElementById(mapId);
    const markerElement = document.getElementById(markerScriptId);

    if (!mapElement || !markerElement || !window.L) {
        return;
    }

    let markers = [];
    try {
        markers = JSON.parse(markerElement.textContent || "[]");
    } catch (error) {
        markers = [];
    }

    const map = L.map(mapElement, {
        scrollWheelZoom: true,
    }).setView([52.0693, 19.4803], 6);

    L.tileLayer("https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png", {
        maxZoom: 19,
        attribution: '&copy; OpenStreetMap contributors &copy; CARTO',
    }).addTo(map);

    const markerLayer = L.layerGroup().addTo(map);

    function renderMarkers(franchiseSlug) {
        const visibleMarkers = franchiseSlug
            ? markers.filter(function (marker) { return marker.franchiseSlug === franchiseSlug; })
            : markers;
        const bounds = [];

        markerLayer.clearLayers();
        visibleMarkers.forEach(function (marker) {
            if (typeof marker.lat !== "number" || typeof marker.lng !== "number") {
                return;
            }

            const popup = [
                "<strong>" + escapeHtml(marker.franchiseName || "") + "</strong>",
                "<span>" + escapeHtml(marker.city || "") + "</span>",
                "<small>" + escapeHtml(marker.category || "") + "</small>",
                '<a href="' + encodeURI(marker.url || "#") + '">Zobacz szczegóły</a>',
            ].join("<br>");

            L.marker([marker.lat, marker.lng]).addTo(markerLayer).bindPopup(popup);
            bounds.push([marker.lat, marker.lng]);
        });

        if (bounds.length) {
            map.fitBounds(bounds, { padding: [36, 36], maxZoom: 12 });
        }
    }

    renderMarkers();
    window.franchiseMapController = {
        filterByFranchise: renderMarkers,
    };

    setTimeout(function () {
        map.invalidateSize();
    }, 100);

    setTimeout(function () {
        map.invalidateSize();
    }, 500);

    window.addEventListener("resize", function () {
        map.invalidateSize();
    });
};

window.loadFranchiseMap = function loadFranchiseMap(options) {
    if (window.matchMedia("(max-width: 767px)").matches) {
        return;
    }
    if (window.L) {
        window.initFranchiseMap(options);
        return;
    }

    const existingScript = document.querySelector('script[data-leaflet-loader="true"]');
    if (existingScript) {
        existingScript.addEventListener("load", function () {
            window.initFranchiseMap(options);
        }, { once: true });
        return;
    }

    const script = document.createElement("script");
    script.src = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.js";
    script.dataset.leafletLoader = "true";
    script.onload = function () {
        window.initFranchiseMap(options);
    };
    document.head.appendChild(script);
};

window.initFranchiseCards = function initFranchiseCards(root) {
    const scope = root || document;
    const cards = Array.from(scope.querySelectorAll("[data-franchise-card]"));

    function setExpanded(card, isExpanded) {
        const details = card.querySelector("[data-franchise-details]");
        const toggle = card.querySelector("[data-franchise-toggle]");
        const label = card.querySelector("[data-franchise-toggle-label]");

        card.setAttribute("aria-expanded", String(isExpanded));
        card.classList.toggle("is-expanded", isExpanded);

        if (details) {
            details.hidden = !isExpanded;
        }
        if (toggle) {
            toggle.setAttribute("aria-expanded", String(isExpanded));
        }
        if (label) {
            label.textContent = isExpanded ? "Zwiń szczegóły" : "Pokaż więcej";
        }
    }

    function toggleCard(card) {
        const shouldExpand = card.getAttribute("aria-expanded") !== "true";

        cards.forEach(function (otherCard) {
            if (otherCard !== card) {
                setExpanded(otherCard, false);
            }
        });
        setExpanded(card, shouldExpand);

        if (window.franchiseMapController) {
            window.franchiseMapController.filterByFranchise(
                shouldExpand ? card.dataset.franchiseSlug : null,
            );
        }
    }

    cards.forEach(function (card) {
        if (card.dataset.franchiseCardReady === "true") {
            return;
        }
        card.dataset.franchiseCardReady = "true";

        const toggle = card.querySelector("[data-franchise-toggle]");
        if (toggle) {
            toggle.addEventListener("click", function (event) {
                event.preventDefault();
                event.stopPropagation();
                toggleCard(card);
            });
        }

        card.addEventListener("click", function (event) {
            if (event.target.closest("a, button, input, label, form")) {
                return;
            }
            toggleCard(card);
        });
    });
};

function escapeHtml(value) {
    return String(value)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}
