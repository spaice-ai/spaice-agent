#!/usr/bin/env python3
"""
Instinct Learning Manager
Handles creation, updating, and management of learned instincts
"""

import json
import sys
import os
from datetime import datetime, timedelta
import uuid

INSTINCTS_FILE = os.path.expanduser("~/.openclaw/workspace/.learnings/instincts.json")

def load_instincts():
    """Load instincts from JSON file"""
    if not os.path.exists(INSTINCTS_FILE):
        return {"version": "1.0", "last_decay_check": None, "instincts": []}
    
    with open(INSTINCTS_FILE, 'r') as f:
        return json.load(f)

def save_instincts(data):
    """Save instincts to JSON file"""
    os.makedirs(os.path.dirname(INSTINCTS_FILE), exist_ok=True)
    with open(INSTINCTS_FILE, 'w') as f:
        json.dump(data, f, indent=2)

def find_instinct(data, domain, instinct_text):
    """Find existing instinct by domain and text similarity"""
    for i, instinct in enumerate(data['instincts']):
        if (instinct['domain'] == domain and 
            instinct_text.lower() in instinct['instinct'].lower()):
            return i
    return None

def create_instinct(instinct_text, category, domain, context=""):
    """Create or update an instinct"""
    data = load_instincts()
    
    existing_idx = find_instinct(data, domain, instinct_text)
    
    if existing_idx is not None:
        # Update existing instinct
        instinct = data['instincts'][existing_idx]
        instinct['confidence'] = min(1.0, instinct['confidence'] + 0.1)
        instinct['last_confirmed'] = datetime.now().isoformat()
        instinct['confirmation_count'] += 1
        print(f"Updated instinct confidence to {instinct['confidence']:.1f}")
    else:
        # Create new instinct
        new_instinct = {
            "id": str(uuid.uuid4())[:8],
            "category": category,
            "domain": domain,
            "instinct": instinct_text,
            "context": context,
            "confidence": 0.3,
            "created": datetime.now().isoformat(),
            "last_confirmed": datetime.now().isoformat(),
            "confirmation_count": 1,
            "examples": []
        }
        data['instincts'].append(new_instinct)
        print(f"Created new instinct: {instinct_text}")
    
    save_instincts(data)

def apply_decay():
    """Apply confidence decay to instincts not confirmed in 30 days"""
    data = load_instincts()
    now = datetime.now()
    decay_threshold = now - timedelta(days=30)
    
    decayed_count = 0
    for instinct in data['instincts']:
        last_confirmed = datetime.fromisoformat(instinct['last_confirmed'].replace('Z', '+00:00'))
        if last_confirmed < decay_threshold:
            old_confidence = instinct['confidence']
            instinct['confidence'] = max(0.0, instinct['confidence'] - 0.05)
            if old_confidence != instinct['confidence']:
                decayed_count += 1
    
    data['last_decay_check'] = now.isoformat()
    save_instincts(data)
    
    if decayed_count > 0:
        print(f"Applied decay to {decayed_count} instincts")

def review_instincts():
    """Review all instincts and show promotion candidates"""
    data = load_instincts()
    
    if not data['instincts']:
        print("No instincts learned yet.")
        return
    
    print("=== LEARNED INSTINCTS ===\n")
    
    promotion_candidates = []
    
    for instinct in sorted(data['instincts'], key=lambda x: x['confidence'], reverse=True):
        confidence_bar = "█" * int(instinct['confidence'] * 10) + "░" * (10 - int(instinct['confidence'] * 10))
        
        print(f"[{confidence_bar}] {instinct['confidence']:.1f}")
        print(f"Domain: {instinct['domain']}")
        print(f"Category: {instinct['category']}")
        print(f"Instinct: {instinct['instinct']}")
        print(f"Confirmations: {instinct['confirmation_count']}")
        
        if instinct['confidence'] > 0.7:
            promotion_candidates.append(instinct)
            print("🚀 PROMOTION CANDIDATE - Consider adding to AGENTS.md or SOUL.md")
        
        print()
    
    if promotion_candidates:
        print(f"\n=== {len(promotion_candidates)} READY FOR PROMOTION ===")
        for candidate in promotion_candidates:
            print(f"• {candidate['instinct']} (confidence: {candidate['confidence']:.1f})")

def main():
    if len(sys.argv) < 2:
        print("Usage:")
        print("  instinct-learn \"instinct text\" category domain [context]")
        print("  instinct-review")
        print("  instinct-decay")
        sys.exit(1)
    
    command = sys.argv[1]
    
    if command == "learn" and len(sys.argv) >= 5:
        instinct_text = sys.argv[2]
        category = sys.argv[3]
        domain = sys.argv[4]
        context = sys.argv[5] if len(sys.argv) > 5 else ""
        create_instinct(instinct_text, category, domain, context)
    
    elif command == "review":
        review_instincts()
    
    elif command == "decay":
        apply_decay()
    
    else:
        print("Invalid command. Use: learn, review, or decay")
        sys.exit(1)

if __name__ == "__main__":
    main()