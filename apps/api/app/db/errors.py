class DatabaseConnectionError(RuntimeError):
    """Raised when the application cannot establish a database connection.

    The message deliberately carries only non-secret diagnostic details
    (host, port, database name, underlying error class) and never the raw
    connection URL or credentials — see check_connection() in session.py.
    """
