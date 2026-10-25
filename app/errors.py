class UserInputError(Exception):
    """A problem we can explain to the person using the bot."""


class NotFoundError(UserInputError):
    """The id is missing, or it belongs to someone else."""
