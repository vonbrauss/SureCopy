#!/bin/bash
# Publish SureCopy to GitHub: source to github.com/vonbrauss/SureCopy (main branch) and the
# Mac app as a release download. Safe to run again: it only adds what's missing.
# Needs GitHub CLI once: brew install gh
set -euo pipefail
cd "$(dirname "$0")/.."
OWNER=vonbrauss; REPO=SureCopy; V=0.2.0; TAG=v$V
APP="dist/SureCopy.app"; ZIP="dist/SureCopy-0.2.0-macOS-arm64.zip"
DESC="Confident, resumable copies for post production: several destinations at once, stop and resume any time."

command -v gh >/dev/null || { echo "GitHub CLI is missing. Install it with: brew install gh"; exit 1; }
gh auth status >/dev/null 2>&1 || gh auth login --hostname github.com --git-protocol https --web
gh auth setup-git

[ -d .git ] || git init -q -b main
# Commit as your GitHub account with its private noreply email, unless git already has an identity.
if ! git config user.email >/dev/null; then
  LOGIN=$(gh api user --jq .login); ID=$(gh api user --jq .id)
  git config user.name "$(gh api user --jq '.name // .login')"
  git config user.email "$ID+$LOGIN@users.noreply.github.com"
fi
git add -A
git diff --cached --quiet || git commit -q -m "SureCopy $V"

if gh repo view "$OWNER/$REPO" >/dev/null 2>&1; then
  git remote get-url origin >/dev/null 2>&1 || git remote add origin "https://github.com/$OWNER/$REPO.git"
  git push -u origin main
else
  gh repo create "$OWNER/$REPO" --public --description "$DESC" --source=. --remote=origin --push
fi

[ -d "$APP" ] || { echo "Build the app first: $APP not found"; exit 1; }
[ -f "$ZIP" ] || ditto -c -k --keepParent "$APP" "$ZIP"
( cd "$(dirname "$ZIP")" && shasum -a 256 "$(basename "$ZIP")" > "$(basename "$ZIP").sha256" )
ASSETS=("$ZIP" "$ZIP.sha256")
if gh release view "$TAG" -R "$OWNER/$REPO" >/dev/null 2>&1; then
  gh release upload "$TAG" "${ASSETS[@]}" -R "$OWNER/$REPO" --clobber
else
  gh release create "$TAG" "${ASSETS[@]}" -R "$OWNER/$REPO" --title "SureCopy $V" --notes-file "packaging/RELEASE_NOTES_GITHUB.md"
fi
echo "Done: https://github.com/$OWNER/$REPO/releases/tag/$TAG"
