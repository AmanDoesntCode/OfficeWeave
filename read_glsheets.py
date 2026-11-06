"""Compatibility entry point for the original public Google Sheets importer."""


def read_glsheets(glsheet_link: str) -> list[dict[str, str]]:
    from mail_merger.spreadsheets import public_sheet_records
    return public_sheet_records(glsheet_link)
