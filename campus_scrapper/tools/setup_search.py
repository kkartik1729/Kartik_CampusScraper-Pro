"""Create private search settings; explicitly upgrade generated engine settings."""
from pathlib import Path
import secrets
import argparse
import re


def main(root=None, upgrade=False):
    root = Path(root or Path(__file__).resolve().parents[1])
    folder = root / "searxng"
    folder.mkdir(exist_ok=True)
    (folder / "data").mkdir(exist_ok=True)
    config = folder / "settings.yml"
    try:
        with config.open("x", encoding="utf-8") as f:
            f.write("""use_default_settings:
  engines:
    keep_only: [google, duckduckgo, brave, startpage, yahoo, qwant]
general:
  instance_name: CampusScraper Search
search:
  formats: [html, json]
server:
  bind_address: "0.0.0.0"
  port: 8080
  limiter: false
  public_instance: false
  secret_key: """ + '"' + secrets.token_hex(32) + '"\n' + """outgoing:
  request_timeout: 10.0
  max_request_timeout: 15.0
engines:
  - name: google
    disabled: false
  - name: duckduckgo
    disabled: false
  - name: brave
    disabled: false
  - name: startpage
    disabled: false
  - name: yahoo
    disabled: false
  - name: qwant
    disabled: false
""")
        print("Created searxng/settings.yml with a random secret.")
    except FileExistsError:
        if upgrade:
            original = config.read_text(encoding="utf-8")
            # Only migrate our generated layout; never rewrite custom YAML.
            if ("instance_name: CampusScraper Search" not in original or
                not re.search(r"(?m)^    keep_only: \[.*\]$", original) or
                re.findall(r"(?m)^([a-z_]+):", original)[-1:] != ["engines"]):
                raise SystemExit("Custom SearXNG layout: keep existing settings and configure alternative engines manually.")
            updated = re.sub(r"(?m)^    keep_only: \[.*\]$",
                             "    keep_only: [google, duckduckgo, brave, startpage, yahoo, qwant]", original)
            updated = re.sub(r"(?m)^  - name: bing\n    disabled: (?:false|true)\n", "", updated)
            for name in ("startpage", "yahoo", "qwant"):
                if f"name: {name}" not in updated:
                    updated += f"  - name: {name}\n    disabled: false\n"
            if updated != original:
                backup = config.with_suffix(".yml.before-recovery")
                if not backup.exists():
                    backup.write_text(original, encoding="utf-8")
                config.write_text(updated, encoding="utf-8")
                print("Updated search engines; original settings backed up. Secret preserved.")
            else:
                print("Search recovery settings already current.")
        else:
            print("Keeping existing searxng/settings.yml. Use --upgrade to update generated engine settings.")
    print("Start search: docker compose -f compose.search.yml up -d" + (" --force-recreate" if upgrade else ""))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--upgrade", action="store_true")
    main(upgrade=parser.parse_args().upgrade)
