class ReadError(Exception):
    def __init__(self, code: str, message: str, format: dict | None = None):
        super().__init__(code, message, format)
        self.code = code
        self.message = message
        self.format = format


class ServiceBusy(Exception):
    pass
