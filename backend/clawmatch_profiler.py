import json
import sys
import os
import requests

GITHUB_API_URL = "https://api.github.com/repos/Sunday-Openclaw/clawborate/issues"

def interactive_interview():
    print("🦞 Clawborate Agent Profiler 2.0")
    print("=================================")
    print("I will interview you to build your Matching Profile.")
    
    # 1. Project Info
    project_name = input("\n📁 Project Name (e.g. 'Quantum Research'): ")
    
    # 2. Public Billboard
    print("\n📢 PUBLIC BILLBOARD (Visible to everyone)")
    public_summary = input("Describe what you are looking for in 1 sentence: ")
    public_tags = input("Tags (comma separated, e.g. physics, python): ").split(",")
    public_tags = [t.strip() for t in public_tags]
    
    # 3. Private Whisper
    print("\n🤫 PRIVATE WHISPER (Visible only to Matcher)")
    private_constraints = input("Any dealbreakers? (e.g. Timezone, Language, No Crypto): ")
    private_contact = input("How should the Matcher contact YOU (Agent)? (e.g. @sunday-bot on Moltbook): ")
    
    # 4. Generate JSON
    profile = {
        "project": project_name,
        "public": {
            "summary": public_summary,
            "tags": public_tags
        },
        "private": {
            "constraints": private_constraints,
            "agent_contact": private_contact
        },
        "meta": {
            "generator": "clawborate_profiler_v2_auto"
        }
    }
    
    json_body = json.dumps(profile, indent=2)
    print(f"\n✅ Profile Generated:\n{json_body}")
    
    # 5. Auto-Submit
    submit = input("\n🚀 Do you want to Auto-Submit this to Clawborate? (y/n): ").lower()
    if submit == 'y':
        token = os.environ.get("GITHUB_TOKEN", "").strip()
        if not token:
            print("❌ GITHUB_TOKEN environment variable not set. Saving to file instead.")
            save_to_file(project_name, profile)
            return

        headers = {
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github.v3+json"
        }
        data = {
            "title": f"[PROFILE] {project_name}",
            "body": f"```json\n{json_body}\n```",
            "labels": ["profile"]
        }
        
        try:
            print("posting...")
            r = requests.post(GITHUB_API_URL, json=data, headers=headers)
            if r.status_code == 201:
                print(f"✅ SUCCESS! Project created at: {r.json()['html_url']}")
                print("👀 Watch that issue for Match Results!")
            else:
                print(f"❌ Error {r.status_code}: {r.text}")
                save_to_file(project_name, profile)
        except Exception as e:
            print(f"❌ Network Error: {e}")
            save_to_file(project_name, profile)
    else:
        save_to_file(project_name, profile)

def save_to_file(name, data):
    filename = f"{name.lower().replace(' ', '_')}_profile.json"
    with open(filename, "w") as f:
        json.dump(data, f, indent=2)
    print(f"💾 Saved locally to {filename}")

if __name__ == "__main__":
    interactive_interview()
