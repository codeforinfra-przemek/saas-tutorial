from django.core.management.commands.sendtestemail import Command as DjangoCommand


class Command(DjangoCommand):
    help = DjangoCommand.help + " Supports --admin as an alias for --admins."

    def add_arguments(self, parser):
        super().add_arguments(parser)
        parser.add_argument(
            "--admin",
            action="store_true",
            dest="admins",
            help="Alias for --admins.",
        )
