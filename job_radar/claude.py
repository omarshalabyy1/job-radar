"""The one Claude client the Claude steps use (grade, tailor, applications), and the one test for an empty balance."""


def client():
    """The Anthropic client: a call stuck for 120 s gives up and is tried once more, which fits inside every Claude
    task's Airflow timeout. The SDK is imported here, not at the top: it is most of the import time, and only a run
    that calls Claude needs it."""
    import anthropic

    return anthropic.Anthropic(timeout=120, max_retries=1)


def out_of_credit(error: Exception) -> bool:
    """Whether an API error says the balance ran out: every further call would be refused too."""
    return "credit" in str(error).lower()
