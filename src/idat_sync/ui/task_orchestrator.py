from idat_sync.services.downloaders import Downloader


class TaskOrchestrator:
    def __init__(self, task_group: list[list[Downloader]]) -> None:
        self.task_group = task_group
