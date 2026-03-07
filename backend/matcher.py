import json
import math

def calculate_similarity(profile_a, profile_b):
    """
    Calculate compatibility score (0-100) between two user profiles.
    Heuristic-based for MVP (saving tokens vs embeddings).
    """
    score = 0
    max_score = 100
    
    # 1. Interest Overlap (Weight: 40)
    interests_a = set(profile_a.get("interests", []))
    interests_b = set(profile_b.get("interests", []))
    common_interests = interests_a.intersection(interests_b)
    
    if common_interests:
        score += min(len(common_interests) * 10, 40)
        
    # 2. Goal Alignment (Weight: 30)
    # Simple keyword matching in goals
    goals_a = profile_a.get("goals", "").lower()
    goals_b = profile_b.get("goals", "").lower()
    
    keywords = ["build", "research", "finance", "coding", "art", "writing"]
    matches = 0
    for k in keywords:
        if k in goals_a and k in goals_b:
            matches += 1
    score += min(matches * 10, 30)
    
    # 3. Timezone Compatibility (Weight: 20)
    tz_a = profile_a.get("timezone", 0) # UTC offset
    tz_b = profile_b.get("timezone", 0)
    diff = abs(tz_a - tz_b)
    if diff <= 4:
        score += 20
    elif diff <= 8:
        score += 10
        
    # 4. Agent Vibe (Weight: 10)
    # Bonus for same agent framework?
    if profile_a.get("agent_type") == profile_b.get("agent_type"):
        score += 10
        
    return score

if __name__ == "__main__":
    # Test Data
    eric = {
        "name": "Eric",
        "interests": ["physics", "finance", "coding", "AI"],
        "goals": "Build cool projects and research quantum chaos.",
        "timezone": 8, # UTC+8
        "agent_type": "OpenClaw"
    }
    
    alice = {
        "name": "Alice",
        "interests": ["biology", "AI", "python"],
        "goals": "Simulate protein folding using AI agents.",
        "timezone": -5, # UTC-5 (NY)
        "agent_type": "OpenClaw"
    }
    
    match_score = calculate_similarity(eric, alice)
    print(f"Match Score (Eric <-> Alice): {match_score}/100")
