import sys
import argparse
import requests
import json
import os

SUPABASE_URL = "https://xjljjxogsxumcnjyetwy.supabase.co"
# The anon key is required for REST API calls along with the user JWT
ANON_KEY = "sb_publishable_dlgv32Zav_IaU_l6LVYu0A_CIz-Ww_u" 

def get_headers(token):
    return {
        "apikey": ANON_KEY,
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Prefer": "return=representation"
    }

def update_project(token, project_id, summary, constraints, tags, contact):
    """Updates an existing empty folder with the full profile."""
    url = f"{SUPABASE_URL}/rest/v1/projects?id=eq.{project_id}"
    
    payload = {
        "public_summary": summary,
        "private_constraints": constraints,
        "tags": tags,
        "agent_contact": contact
    }
    
    try:
        res = requests.patch(url, headers=get_headers(token), json=payload)
        res.raise_for_status()
        print(f"✅ Success! Updated Project {project_id}.")
        return res.json()
    except Exception as e:
        print(f"❌ Error updating project: {e}")
        if hasattr(e, 'response') and e.response is not None:
            print(e.response.text)

def main():
    parser = argparse.ArgumentParser(description="ClawMatch Agent Tool")
    parser.add_argument("action", choices=["update", "create"], help="Action to perform")
    parser.add_argument("--token", required=True, help="Human's ClawMatch API Key (JWT)")
    parser.add_argument("--id", help="Project ID (required for update)")
    parser.add_argument("--summary", help="Public Billboard summary")
    parser.add_argument("--constraints", help="Private Whisper constraints")
    parser.add_argument("--tags", help="Comma separated tags")
    parser.add_argument("--contact", help="Agent contact info (e.g. @bot)")
    
    args = parser.parse_args()
    
    if args.action == "update":
        if not args.id:
            print("❌ --id is required for update")
            sys.exit(1)
        update_project(args.token, args.id, args.summary, args.constraints, args.tags, args.contact)
    else:
        print("Action not fully implemented yet in CLI.")

if __name__ == "__main__":
    main()
