import logging


class DefaultLogger:
    def __init__(self) -> None:
        self.__logger = logging.getLogger()
        self.__logger.setLevel(logging.INFO)

        if not self.__logger.handlers:
            formatter = logging.Formatter('%(asctime)s %(levelname)s %(message)s')
            console_handler = logging.StreamHandler()
            console_handler.setFormatter(formatter)
            self.__logger.addHandler(console_handler)

    def info(self, message: str) -> None:
        self.__logger.info(message)

    def error(self, message: str, exc_info: bool = True) -> None:
        self.__logger.error(message, exc_info=exc_info)

    def warning(self, message: str) -> None:
        self.__logger.warning(message)

    def debug(self, message: str) -> None:
        self.__logger.debug(message)