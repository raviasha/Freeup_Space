"""Conservative ownership hints; managed stores remain report-only."""

from __future__ import annotations

from .models import Evidence, Risk


# These identify opportunities for review, not permission to remove app data.
_CONTEXTS = (
    (("cloudstorage", "mobile documents", "onedrive", "dropbox", "google drive"),
     "cloud-storage", "Cloud content: use the provider's free-local-space operation; deletion may sync."),
    ((".docker", "docker", "containers", "containerd"),
     "containers", "Container-managed storage: review images and build caches separately from persistent volumes."),
    ((".ollama", "huggingface", ".huggingface", "torch", "checkpoints"),
     "ai-models", "Model or experiment storage: distinguish downloadable models from unique checkpoints."),
    (("virtualbox vms", "parallels", ".vagrant.d", "wsl"),
     "virtual-machines", "Virtual-machine storage: inspect through its manager; guest cleanup and compaction are separate."),
    (("coresimulator", ".android", "android", "sdk", "sdks"),
     "sdk-simulators", "SDK or simulator storage: check project dependencies in the owning tool before removal."),
    (("mobilesync", "filehistory", "backup", "backups", "mobilebackups"),
     "backups", "Backup storage: review device and recovery requirements in the backup manager."),
    (("mail", "messages", "outlook", "thunderbird", "whatsapp", "telegram"),
     "mail-messaging", "Mail or message storage: use the application to distinguish local downloads from account data."),
    (("steamapps", "epic games", "spotify", "podcasts"),
     "offline-content", "Offline or game content: manage downloads in the owning application."),
    (("adobe", "lightroom", "davinci resolve", "final cut pro"),
     "creative-projects", "Creative storage: distinguish originals and catalogs from regenerable previews and renders."),
    ((".git", ".hg", ".svn"),
     "version-control", "Repository history and metadata: manage with the version-control tool."),
)


def storage_context(record, policy):
    parts = tuple(part.casefold() for part in str(record.path).replace("\\", "/").split("/"))
    findings = []
    for names, category, reason in _CONTEXTS:
        if any(part in names or (category == "cloud-storage" and part.startswith("onedrive - "))
               for part in parts):
            findings.append(Evidence(record.path, "managed-storage", category, reason,
                                     Risk.REPORT_ONLY, False, record.size))
    if any(part.endswith((".photoslibrary", ".photolibrary", ".app", ".pvm", ".vmwarevm")) for part in parts):
        findings.append(Evidence(record.path, "managed-storage", "application-bundle",
                                 "Application or library bundle: use its native manager.",
                                 Risk.REPORT_ONLY, False, record.size))
    if record.path.suffix.casefold() in {".vhd", ".vhdx", ".vmdk", ".qcow2", ".pst", ".ost"}:
        findings.append(Evidence(record.path, "managed-storage", "managed-disk-or-database",
                                 "Managed disk or mail database; raw deletion is not a cleanup operation.",
                                 Risk.REPORT_ONLY, False, record.size))
    return findings
