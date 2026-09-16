# Offline verification

## Commands and results

- `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s ../outputs/ye-job-search-application/tests -p 'test_*.py' -v` — exit 0; 27 synthetic tests passed. Coverage includes complete config/live-projection acceptance; missing bindings; equally malformed application/company/page contracts; URL slug/UUID/view variants; wrong-origin, malformed, conflicting-identity, and side-peek rejection; valid synthetic config-only relocation; normal queueing and recovery; deduplication; destination, application, page, and receipt-identity conflicts; and unknown-page resolution only with matching application and destination identity; unsupported history storage and invalid column references; malformed percent encodings; receipt/pending conflicts in all three paths; and consistent receipt recovery without duplication.
- `PYTHONDONTWRITEBYTECODE=1 python3 -m json.tool ../outputs/ye-job-search-application/config/notion.json >/dev/null` — exit 0; candidate config parsed as JSON.
- `ruby -e 'require "yaml"; ARGV.each { |path| body = File.read(path).sub(/\A---\s*\n/, ""); frontmatter = body.split(/^---\s*$/, 2).first; data = YAML.safe_load(frontmatter); abort "invalid frontmatter: #{path}" unless data.is_a?(Hash) && data["name"].is_a?(String) && !data["name"].empty? }' ../outputs/ye-job-search-application/SKILL.md ../outputs/ye-company-networking-poc/SKILL.md` — exit 0; both Skill frontmatter documents parsed and include names.
- `find ../outputs -type f -print | sed 's#^../outputs/##' | sort` — returned exactly the nine approved candidate paths.

## Limitations

- These checks use synthetic host-normalized observations and in-memory queue snapshots only. They do not access Notion, a browser, a live queue file, mail, LinkedIn, or application sites.
- URL normalization verifies only the approved Notion origin and embedded page identity. It cannot prove live URL resolution, aliases, permissions, page content, access, privacy, or a dismissed-editor readback.
- The supplied host observations were recorded in the candidate config with their stated scope. They do not establish a fresh run, and the Application Archive and Company PoC notes do not claim every property type or status option was rechecked.
- No scheduler, API, integration, historical import, send, submission, or external message was attempted. No candidate was applied to a live Skill path.
- DeepSeek completion verification was not run in this delegated stage: the controller prohibits network/model launch and reserves fresh independent completion verification and any application decision to the host.
