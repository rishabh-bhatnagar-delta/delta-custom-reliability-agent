class MCPAuditorError(Exception):
    """Base exception for all errors in the AWS Auditor MCP server."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class AWSConnectionError(MCPAuditorError):
    """Raised when the Boto3 session or client cannot be established."""
    pass


class ResourceFetchError(MCPAuditorError):
    """Raised when CloudFormation fails to list stacks or resources."""
    pass


class ConfigurationError(MCPAuditorError):
    """Raised when environment variables or .env settings are missing/invalid."""
    pass


class MissingToolParam(MCPAuditorError, ValueError):
    pass
