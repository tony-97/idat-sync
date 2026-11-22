# Course Synchronization

Synchronizes remote course materials and class recordings from IDAT's learning platforms into a structured local directory tree.

## Language

### Curriculum Structure

**Course**:
An enrolled academic subject identified by its academic term category and containing ordered sections of learning material and associated class recordings.
_Avoid_: Subject, class

**CourseSection**:
A thematic or weekly grouping within a course (such as a topic or week block) that has its own summary and a sequence of modules.
_Avoid_: Topic, week, block

**CourseModule**:
A discrete learning activity or item inside a course section (such as a notice, assignment, file resource, or external folder link) containing inline text artifacts and unresolved remote links.
_Avoid_: Activity, lesson

**ModuleContent**:
An unresolved remote resource link attached to a course module, carrying a target filename, relative subfolder path, remote URL, and modification timestamp prior to URL expansion.
_Avoid_: Attachment, raw link

### Synchronization & Files

**Content**:
A resolved, downloadable item with a relative or target local file path and modification timestamp, ready to be compared against local files or written to disk.
_Avoid_: Downloadable, file item, asset

**StringContent**:
An in-memory text artifact (such as a section summary, label notice, or assignment introduction) represented as a sequence of strings ready to be rendered to an HTML file on disk. Subtype of `Content`.
_Avoid_: Text content, inline HTML, note item

**RecordingContent**:
A remote class video recording hosted on SharePoint or Microsoft Teams with a target file path and last modified timestamp, ready to be downloaded by a specialized video downloader. Subtype of `Content`.
_Avoid_: Video, class recording, meeting recording

**SyncEvent**:
An immutable lifecycle notification emitted as a course and its downloadable items progress through synchronization.
_Avoid_: Progress callback, listener, queue tuple

### Authentication

**Credentials**:
The Moodle authentication token and SharePoint storage state obtained at login and reused for course synchronization.
_Avoid_: Session, auth state, login data
