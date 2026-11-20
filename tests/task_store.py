"""Read a generated app's persisted task store (snapshot plus journal) the way the app itself does."""
import json
from pathlib import Path


def read_tasks(path):
    """{task_id: task} for the store at `path`: the snapshot, with every journal line of its epoch applied."""
    path = Path(path)
    tasks, epoch = {}, 0
    if path.exists():
        document = json.loads(path.read_text(encoding="utf-8"))
        tasks, epoch = dict(document["tasks"]), int(document.get("epoch", 0))
    journal = Path(str(path) + ".journal")
    if journal.exists():
        for line in journal.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                entry = json.loads(line)
            except ValueError:
                continue  # a torn last line
            if entry.get("e") != epoch:
                continue
            if entry["op"] == "put":
                tasks[entry["id"]] = entry["task"]
            elif entry["op"] == "del":
                tasks.pop(entry["id"], None)
    return tasks
