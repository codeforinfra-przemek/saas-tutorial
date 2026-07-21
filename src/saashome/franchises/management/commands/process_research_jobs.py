import time

from django.core.management.base import BaseCommand

from franchises.research_jobs import (
    claim_next_job,
    fail_stale_jobs,
    process_research_job,
)
from franchises.research_launches import (
    claim_next_launch,
    fail_stale_launches,
    process_research_launch,
)


class Command(BaseCommand):
    help = "Process queued Human Research Workbench jobs outside web requests."

    def add_arguments(self, parser):
        parser.add_argument(
            "--once",
            action="store_true",
            help="Process at most one queued job and exit.",
        )
        parser.add_argument(
            "--poll-seconds",
            type=float,
            default=2.0,
            help="Queue polling interval in worker mode (default: 2).",
        )

    def handle(self, *args, **options):
        once = options["once"]
        poll_seconds = max(0.25, min(options["poll_seconds"], 30.0))
        self.stdout.write("Research worker is ready.")
        while True:
            fail_stale_jobs()
            fail_stale_launches()
            launch = claim_next_launch()
            if launch is not None:
                self.stdout.write(
                    f"Processing initial launch {launch.launch_id}: "
                    f"{launch.profile_id} for {launch.franchise.slug}."
                )
                process_research_launch(launch)
                launch.refresh_from_db()
                if launch.status == launch.STATUS_SUCCEEDED:
                    self.stdout.write(
                        self.style.SUCCESS(f"Launch {launch.launch_id} succeeded.")
                    )
                else:
                    self.stderr.write(
                        self.style.ERROR(
                            f"Launch {launch.launch_id} failed: "
                            f"{launch.error_code} {launch.error_message}"
                        )
                    )
                if once:
                    return
                continue
            job = claim_next_job()
            if job is None:
                if once:
                    self.stdout.write("No queued research jobs.")
                    return
                time.sleep(poll_seconds)
                continue
            self.stdout.write(
                f"Processing {job.job_id}: {job.get_kind_display()} "
                f"for {job.workspace.franchise.slug}."
            )
            process_research_job(job)
            job.refresh_from_db()
            if job.status == job.STATUS_SUCCEEDED:
                self.stdout.write(self.style.SUCCESS(f"Job {job.job_id} succeeded."))
            else:
                self.stderr.write(
                    self.style.ERROR(
                        f"Job {job.job_id} failed: {job.error_code} "
                        f"{job.error_message}"
                    )
                )
            if once:
                return
