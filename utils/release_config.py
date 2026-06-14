from dataclasses import dataclass
import json
import os
from string import Template


PLACEHOLDER_OWNER = "OWNER_TODO"
PLACEHOLDER_REPO = "REPO_TODO"
PLACEHOLDER_HF_REPO_ID = "HF_REPO_TODO"


@dataclass(frozen=True)
class ReleaseConfig:
    github_owner: str
    github_repo: str
    hf_repo_id: str
    release_asset_zip_name: str
    latest_json_asset_name: str
    installer_name_pattern: str

    @classmethod
    def placeholder(cls):
        return cls(
            github_owner=PLACEHOLDER_OWNER,
            github_repo=PLACEHOLDER_REPO,
            hf_repo_id=PLACEHOLDER_HF_REPO_ID,
            release_asset_zip_name="VRCNT-Next.zip",
            latest_json_asset_name="latest.json",
            installer_name_pattern="VRCNT-Next_${version}_x64-setup.exe",
        )

    @property
    def has_placeholders(self):
        return (
            self.github_owner in ("", PLACEHOLDER_OWNER)
            or self.github_repo in ("", PLACEHOLDER_REPO)
            or self.hf_repo_id in ("", PLACEHOLDER_HF_REPO_ID)
        )


@dataclass(frozen=True)
class ReleaseUrls:
    release_url: str
    latest_json_url: str
    installer_url: str
    release_zip_url: str
    raw_package_json_url: str
    has_placeholders: bool


def _repo_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_release_config(root=None):
    root = root or _repo_root()
    config_path = os.path.join(root, "release.config.json")
    fallback = ReleaseConfig.placeholder()
    try:
        with open(config_path, "r", encoding="utf-8") as fp:
            data = json.load(fp)
    except Exception:
        return fallback

    return ReleaseConfig(
        github_owner=str(data.get("githubOwner", fallback.github_owner)).strip(),
        github_repo=str(data.get("githubRepo", fallback.github_repo)).strip(),
        hf_repo_id=str(data.get("hfRepoId", fallback.hf_repo_id)).strip(),
        release_asset_zip_name=str(data.get("releaseAssetZipName", fallback.release_asset_zip_name)).strip(),
        latest_json_asset_name=str(data.get("latestJsonAssetName", fallback.latest_json_asset_name)).strip(),
        installer_name_pattern=str(data.get("installerNamePattern", fallback.installer_name_pattern)).strip(),
    )


def build_release_urls(config, version):
    if config.has_placeholders:
        return ReleaseUrls(
            release_url="",
            latest_json_url="",
            installer_url="",
            release_zip_url="",
            raw_package_json_url="",
            has_placeholders=True,
        )

    owner = config.github_owner
    repo = config.github_repo
    hf_repo_id = config.hf_repo_id
    version = str(version).strip().lstrip("v")
    tag = f"v{version}"
    installer_name = Template(config.installer_name_pattern).safe_substitute(version=version)
    hf_base = f"https://huggingface.co/{hf_repo_id}/resolve"

    return ReleaseUrls(
        release_url=f"https://github.com/{owner}/{repo}/releases",
        latest_json_url=f"{hf_base}/main/{config.latest_json_asset_name}",
        installer_url=f"{hf_base}/{tag}/{installer_name}",
        release_zip_url=f"{hf_base}/{tag}/{config.release_asset_zip_name}",
        raw_package_json_url=f"https://raw.githubusercontent.com/{owner}/{repo}/main/package.json",
        has_placeholders=False,
    )
