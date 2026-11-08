from idat_sync.services.downloaders import Content, Downloader


class DownloadManager:
    def __init__(self, strategies: list[Downloader]) -> None:
        self.strategies = strategies

    def create_download_task(self, content: Content):
        for downloader in self.strategies:
            if task := content.generate_task(downloader):
                return task
