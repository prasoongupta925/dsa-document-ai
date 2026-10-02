"""uvicorn's access log without query strings.

uvicorn logs each request line with its query string, and on Lambda those lines
go to CloudWatch, where an applicant erase cannot reach them. Some query strings
carry personal data: GET .../eligibility/inputs?applicant= (a PAN or a name),
.../eligibility/companies?name= (an employer), the ?key= of the document and
artifact download URLs (file names) and the graph ?search= terms. The filter
keeps the client, method, path, HTTP version and status of every line and drops
everything from "?" on.
"""

import logging

ACCESS_LOGGER = "uvicorn.access"


class StripQueryString(logging.Filter):
    """uvicorn logs (client, method, path?query, http version, status) as the record's args."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(arg.split("?", 1)[0] if isinstance(arg, str) else arg for arg in record.args)
        return True


def install() -> None:
    """Add the filter to uvicorn's access logger (once)."""
    logger = logging.getLogger(ACCESS_LOGGER)
    if not any(isinstance(f, StripQueryString) for f in logger.filters):
        logger.addFilter(StripQueryString())
