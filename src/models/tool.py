import enum


class ToolNames(str, enum.Enum):
    RESOURCE_FETCHER = "resource_fetcher"
    RESOURCE_FETCHER_BY_STACK_NAME = "resource_fetcher_by_stack_name"
    RESOURCE_FETCHER_BY_BLOCK_CODE = "resource_fetcher_by_block_code"
    GENERATE_AUDIT_REPORT = "generate_audit_report_by_block_code"
    GENERATE_AUDIT_REPORT_BY_STACK_NAME = "generate_audit_report_by_stack_name"
