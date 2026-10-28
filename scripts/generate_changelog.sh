#!/bin/bash
# Generate CHANGELOG.md from GitHub Releases
# Usage:
#   ./scripts/generate_changelog.sh > CHANGELOG.md
#   ./scripts/generate_changelog.sh --tag 0.57.0 --prepend-to CHANGELOG.md
# Note: --limit 500 may need to be increased if releases exceed 500

set -euo pipefail

REPO="datamodel-code-generator/datamodel-code-generator"
TAG=""
PREPEND_TO=""

while [ "$#" -gt 0 ]; do
    case "$1" in
        --repo)
            REPO="$2"
            shift 2
            ;;
        --tag)
            TAG="$2"
            shift 2
            ;;
        --prepend-to)
            PREPEND_TO="$2"
            shift 2
            ;;
        *)
            echo "Unknown argument: $1" >&2
            exit 2
            ;;
    esac
done

normalize_release_body() {
    awk '
        {
            sub(/\r$/, "")
            if ($0 == "") {
                if (blank < 2) {
                    blank += 1
                }
                next
            }
            while (blank > 0 && seen) {
                print ""
                blank -= 1
            }
            seen = 1
            blank = 0
            print
        }
    '
}

write_header() {
    echo "# Changelog"
    echo ""
    echo "All notable changes to this project are documented in this file."
    echo "This changelog is automatically generated from GitHub Releases."
    echo ""
    echo "---"
    echo ""
}

write_release_entry() {
    local tag="$1"
    local release_json
    local is_draft
    local date
    local body

    release_json=$(gh release view "$tag" --repo "$REPO" --json body,createdAt,isDraft,publishedAt)
    is_draft=$(jq -r '.isDraft' <<< "$release_json")

    if [ "$is_draft" = "true" ]; then
        echo "Release is a draft: $tag" >&2
        return 1
    fi

    date=$(jq -r '(.publishedAt // .createdAt) | split("T")[0]' <<< "$release_json")
    body=$(jq -r '.body // ""' <<< "$release_json" | normalize_release_body)

    echo "## [$tag](https://github.com/$REPO/releases/tag/$tag) - $date"
    echo ""
    printf '%s\n' "$body"
    echo ""
    echo "---"
    echo ""
}

prepend_release_entry() {
    local tag="$1"
    local changelog="$2"
    local tmp_dir

    tmp_dir=$(mktemp -d)
    trap 'rm -rf "$tmp_dir"' EXIT

    write_release_entry "$tag" > "$tmp_dir/new_entry.md"

    if [ ! -f "$changelog" ]; then
        write_header > "$changelog"
    fi

    awk -v header="$tmp_dir/header.md" -v old_entries="$tmp_dir/old_entries.md" -v tag="$tag" '
        function is_release_heading(line) {
            return index(line, "## [" tag "](") == 1
        }
        /^---$/ && !found {found=1; skip_blank=1; next}
        found && skip_blank && $0 == "" {skip_blank=0; next}
        found && skip_duplicate {
            if ($0 == "---") {
                skip_duplicate=0
                skip_blank=1
            }
            next
        }
        !found {print > header; next}
        found && is_release_heading($0) {
            skip_duplicate=1
            next
        }
        found {skip_blank=0; print > old_entries}
    ' \
        "$changelog"
    touch "$tmp_dir/old_entries.md"
    { cat "$tmp_dir/header.md"; printf '%s\n\n' '---'; cat "$tmp_dir/new_entry.md" "$tmp_dir/old_entries.md"; } > "$changelog"
    rm -rf "$tmp_dir"
    trap - EXIT
}

if [ -n "$PREPEND_TO" ]; then
    if [ -z "$TAG" ]; then
        echo "--prepend-to requires --tag" >&2
        exit 2
    fi
    prepend_release_entry "$TAG" "$PREPEND_TO"
    exit 0
fi

write_header

# Get all releases and process them
gh release list --repo "$REPO" --limit 500 --exclude-drafts --json tagName --jq '.[].tagName' | while read -r tag; do
    write_release_entry "$tag"
done
