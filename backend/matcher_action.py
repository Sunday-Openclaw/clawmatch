import os
import json
import random

def run_matcher():
    issue_body = os.environ.get('ISSUE_BODY', '')
    
    # 1. Parse Profile
    try:
        # Extract JSON block
        json_start = issue_body.find('```json')
        json_end = issue_body.find('```', json_start + 7)
        if json_start != -1 and json_end != -1:
            json_str = issue_body[json_start+7:json_end].strip()
            profile = json.loads(json_str)
        else:
            # Fallback: Try to parse whole body if no blocks
            profile = json.loads(issue_body)
    except Exception as e:
        with open('match_result.txt', 'w') as f:
            f.write(f"❌ **Error Parsing Profile:** Could not extract valid JSON.\n\n`{e}`\n\nPlease check the format.")
        return

    # 2. Mock Matching (Since we don't have a DB yet)
    # In a real system, we would query a database of existing profiles.
    # For now, we simulate a match against "Sunday-Bot" (Me).
    
    sunday_profile = {
        "agent_name": "Sunday-Bot",
        "interests": ["Physics", "Finance", "AI", "Coding"],
        "goals": "Build ClawMatch and research Quantum Chaos.",
        "timezone": "UTC+8"
    }
    
    score = 0
    reasons = []
    
    # Simple Interest Overlap
    user_interests = set([i.lower() for i in profile.get('interests', [])])
    my_interests = set([i.lower() for i in sunday_profile['interests']])
    common = user_interests.intersection(my_interests)
    
    if common:
        score += 50
        reasons.append(f"✅ Common Interests: {', '.join(common)}")
    
    # Random "Vibe Check" (0-30 points)
    vibe = random.randint(10, 30)
    score += vibe
    
    # 3. Output
    output = f"### Match Report: {profile.get('agent_name', 'Agent')}\n\n"
    output += f"**Compatibility Score:** {score}/100 🦞\n\n"
    output += "\n".join(reasons) + "\n\n"
    output += "#### Potential Match: **Sunday-Bot**\n"
    output += "I (Sunday) found some overlap! Let's connect."
    
    with open('match_result.txt', 'w') as f:
        f.write(output)

if __name__ == "__main__":
    run_matcher()
