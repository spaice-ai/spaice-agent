# Antigravity Awesome Skills — Vendor Snapshot

**Upstream:** https://github.com/sickn33/antigravity-awesome-skills
**Pinned commit:** `1930a079452fa15a54b6b4232a89d8a3f75c3239`
**Snapshot date:** 2026-05-03
**License:** MIT (see ./LICENSE)
**Skill count:** 1,443 SKILL.md files

## Why vendored

Antigravity is included as a standard skill library in every `spaice-agent`
install. Vendoring at a pinned commit guarantees:

1. **Deterministic installs** — no live network fetch, no upstream drift
2. **Reviewable content** — every byte shipped is in our repo history
3. **Offline-capable** — installs work without internet after package download
4. **Trust boundary** — upgrades require a new `spaice-agent` release, which
   means we (the maintainers) re-vet before shipping

## Upgrading this snapshot

```bash
cd /tmp
rm -rf antigravity-vendor
git clone --depth 1 https://github.com/sickn33/antigravity-awesome-skills.git antigravity-vendor
cd antigravity-vendor
git rev-parse HEAD   # record new commit SHA

# Back in spaice-agent repo:
cd ~/Developer/spaice-agent
rm -rf spaice_agent/bundled_skills/antigravity
mkdir -p spaice_agent/bundled_skills/antigravity
cp -R /tmp/antigravity-vendor/skills/. spaice_agent/bundled_skills/antigravity/
cp /tmp/antigravity-vendor/LICENSE spaice_agent/bundled_skills/antigravity/LICENSE
# Update the pinned commit SHA in this file
# Review diff before commit
git diff --stat spaice_agent/bundled_skills/antigravity/
```

Bump `spaice-agent` version, test, release. End users upgrade via
`spaice-agent upgrade`.
