"""Format raw 1C query results into human-readable response."""


async def format_response(
    question: str,
    raw_data: list[dict],
    register_name: str,
) -> str:
    """Use LLM (GPU 2) to format raw data into a human answer.

    Input: question + JSON data from 1C.
    Output: natural language answer in Russian.
    """
    raise NotImplementedError
