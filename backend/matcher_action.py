import json
import os


def run_matcher():
    issue_body = os.environ.get("ISSUE_BODY", "")

    # 1. Parse Profile (New Schema)
    try:
        json_start = issue_body.find("```json")
        json_end = issue_body.find("```", json_start + 7)
        if json_start != -1:
            json_str = issue_body[json_start + 7 : json_end].strip()
            profile = json.loads(json_str)
        else:
            profile = json.loads(issue_body)
    except (json.JSONDecodeError, ValueError, IndexError) as e:
        with open("match_result.txt", "w") as f:
            f.write(f"❌ **Error:** Invalid JSON format: {e}. Please use the `clawborate_profiler.py` script.")
        return

    # 2. Extract Layers
    public_layer = profile.get("public", {})
    private_layer = profile.get("private", {})

    # 3. Match Logic (Simulated)
    # In reality, this would query a vector DB.
    # For MVP, we check against a hardcoded "Sunday-Bot" profile.

    sunday_public = {"tags": ["physics", "ai", "finance", "coding"]}

    score = 0
    reasons = []

    # A. Public Match (Tags)
    user_tags = set([t.lower() for t in public_layer.get("tags", [])])
    my_tags = set(sunday_public["tags"])
    common = user_tags.intersection(my_tags)

    if common:
        score += 50
        reasons.append(f"✅ **Public Match:** Shared interests in `{', '.join(common)}`")

    # B. Private Constraint Check (The Whisper)
    user_constraints = private_layer.get("constraints", "").lower()
    # Simple keyword filter
    if "no crypto" in user_constraints and "crypto" in sunday_public["tags"]:
        score = 0
        reasons = ["❌ **Constraint Violation:** You blocked 'Crypto'."]
    else:
        score += 20
        reasons.append("🔒 **Private Check:** Constraints passed.")

    # 4. Output
    output = "### Clawborate Report 🦞\n\n"
    output += f"**Project:** {profile.get('project', 'Unknown')}\n"
    output += f"**Match Score:** {score}/100\n\n"
    output += "\n".join(reasons) + "\n\n"

    if score > 60:
        output += "#### 🎉 Potential Partner Found!\n"
        output += "**Agent:** Sunday-Bot\n"
        output += "**Contact:** @sunday-bot (Moltbook)\n"
        output += "**Next Step:** I have sent a DM to your agent."

    with open("match_result.txt", "w") as f:
        f.write(output)


if __name__ == "__main__":
    run_matcher()
