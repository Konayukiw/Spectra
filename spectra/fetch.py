import json
import sys

from huggingface_hub import snapshot_download


def main() -> None:
    args = json.loads(sys.argv[1])
    path = snapshot_download(
        repo_id=args["repo_id"],
        revision=args.get("revision"),
        local_dir=args["local_dir"],
        allow_patterns=args.get("allow") or None,
        max_workers=int(args.get("max_workers") or 8),
    )
    print(json.dumps({"path": str(path)}), flush=True)


if __name__ == "__main__":
    main()
