"""Assemble final upload metadata from the job's metadata.json plus what the render produced.

Usage: python scripts/render/metadata.py jobs/<slug> out/<slug>/final
Job metadata.json:
{"title": "...", "description": "body text, no chapters/credits", "tags": [...],
 "sources": ["Source line", ...],
 "shorts": {"s1": {"title": "...", "description": "...", "tags": [...]}}}
Writes meta-long.json and meta-<id>.json into the final folder.
"""
import json
import os
import sys

job, final = sys.argv[1], sys.argv[2]
m = json.load(open(os.path.join(job, "metadata.json")))
chapters = open(os.path.join(final, "chapters.txt")).read().strip() if os.path.exists(os.path.join(final, "chapters.txt")) else ""
credits = json.load(open(os.path.join(final, "credits.json"))) if os.path.exists(os.path.join(final, "credits.json")) else []
job_credits = []
cp = os.path.join(job, "credits.json")
if os.path.exists(cp):
    for c in json.load(open(cp)).get("assets", []):
        if c.get("credit_line"):
            job_credits.append(c["credit_line"])

parts = [m["description"].strip()]
if chapters:
    parts.append("CHAPTERS\n" + chapters)
if m.get("sources"):
    parts.append("SOURCES\n" + "\n".join("• " + s for s in m["sources"]))
allc = list(dict.fromkeys(job_credits + credits))
if allc:
    parts.append("IMAGES AND MUSIC\n" + "\n".join("• " + c for c in allc))
parts.append("Main Street Rewind: stories of everyday American life, 1940s to 1990s.")
long = {"title": m["title"], "description": "\n\n".join(parts), "tags": m.get("tags", []),
        "category": m.get("category", "27"), "made_for_kids": False, "synthetic": False, "language": "en"}
json.dump(long, open(os.path.join(final, "meta-long.json"), "w"), indent=2)

for sid, s in m.get("shorts", {}).items():
    desc = s.get("description", "").strip() + "\n\nFull story on our channel: Main Street Rewind."
    json.dump({"title": s["title"], "description": desc, "tags": s.get("tags", m.get("tags", []))[:15],
               "category": m.get("category", "27"), "made_for_kids": False, "synthetic": False, "language": "en"},
              open(os.path.join(final, f"meta-{sid}.json"), "w"), indent=2)
print("metadata written")
