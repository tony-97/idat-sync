from idat_sync.services.downloaders import Downloader, FileDownloadInfoBase


class DownloadManager:
    def __init__(self, strategies: list[Downloader]) -> None:
        self.strategies = strategies

    def list_files(self, parent_path: str, download_content):
        file_list = list[FileDownloadInfoBase]()
        for content in download_content:
            for downloader in self.strategies:
                if (file_url := content.get("fileurl")) and downloader.can_handle(
                    file_url
                ):
                    file_list += downloader.list_files(parent_path, content)
        return file_list
