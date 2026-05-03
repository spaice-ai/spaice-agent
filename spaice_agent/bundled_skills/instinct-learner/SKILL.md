---
name: instinct-learner
description: "Evolution of self-improvement with confidence scoring. Learn from errors, corrections, and discoveries with weighted confidence tracking. Stores instincts as JSON with decay-over-time. Use after errors occur, when receiving corrections, when discovering better approaches, or when identifying knowledge gaps. Pairs with self-improvement skill — self-improvement captures raw learnings, instinct-learner scores them for durability."
---

# Instinct Learner

**Purpose:** Evolution of self-improvement with confidence scoring. Learn from errors, corrections, and discoveries with weighted confidence tracking.

## When to Use

- After any error occurs
- When receiving corrections from humans
- When discovering better approaches
- When identifying knowledge gaps
- During post-mortem analysis

## How It Works

1. **Detect Learning Moment:** Error, correction, discovery, or gap identified
2. **Categorize:** Assign to error, correction, best_practice, or knowledge_gap
3. **Score Confidence:** Start at 0.3, increase with confirmations, decay over time
4. **Store Instinct:** JSON format in ~/.openclaw/workspace/.learnings/instincts.json
5. **Promote When Ready:** Confidence > 0.7 → suggest adding to AGENTS.md/SOUL.md

## Usage

```bash
# Create/update an instinct
instinct-learn "Always use trash instead of rm" "best_practice" "file_safety"

# Check confidence scores
instinct-review

# Promote high-confidence instincts
instinct-promote
```

## Categories

- **error**: Mistakes that caused failure
- **correction**: Human feedback fixing your approach  
- **best_practice**: Better ways discovered
- **knowledge_gap**: Things you didn't know

## Confidence Scoring

- **Start:** 0.3 (tentative)
- **Confirmation:** +0.1 each time pattern proven
- **Decay:** -0.05 if not confirmed in 30 days
- **Promote:** When > 0.7, suggest adding to core documentation

## Instinct Structure

```json
{
  "id": "unique_identifier",
  "category": "best_practice",
  "domain": "file_operations", 
  "instinct": "Use trash command instead of rm for safety",
  "context": "Prevents accidental permanent deletion",
  "confidence": 0.7,
  "created": "2025-01-15T10:30:00Z",
  "last_confirmed": "2025-02-01T15:45:00Z",
  "confirmation_count": 4,
  "examples": ["rm mistake deleted important file", "trash allowed recovery"]
}
```

## Decision Tree

```
Learning Event Detected
├── Categorize (error/correction/best_practice/knowledge_gap)
├── Check if instinct exists
│   ├── YES: Update confidence (+0.1), add example
│   └── NO: Create new instinct (confidence 0.3)
├── Apply decay to all instincts (weekly)
└── Check for promotions (confidence > 0.7)
```

## Implementation

The skill creates `instinct-learn`, `instinct-review`, and `instinct-promote` commands that manage the instincts.json file and suggest promotions to core documentation.

## Auto-Triggers

- After any command fails (error category)
- After human says "actually..." or "no, do this instead" (correction)
- When you discover a more efficient approach (best_practice)  
- When you realize you lack knowledge (knowledge_gap)

Remember: The goal is continuous improvement through pattern recognition and confidence weighting.