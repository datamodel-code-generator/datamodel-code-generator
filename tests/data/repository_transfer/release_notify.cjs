// Execute only the workflow's pure parsing/guard sections. No API client or mocks.
const fs = require('node:fs');
const { script, env, cases } = JSON.parse(fs.readFileSync(0, 'utf8'));
const section = (start, end) => script.slice(script.indexOf(start), script.indexOf(end));
const guard = section('// Target repo', '// Get tag');
const extraction = section('// The old owner', 'const directPRNotifications');
const bodyExtraction = section('// Find referenced PRs in PR body', '// Collect closing issues');
const deduplication = section('function hasReleaseNotification', '// The old owner');
const parse = new Function('repoOwner', 'repoName', 'releaseBody', 'prBody', 'prNumber', `
  const version = '1.2.3';
  ${extraction}
  const closingIssues = extractIssueNumbers(prBody, closingKeywordBase);
  const relatedIssues = extractIssueNumbers(prBody, relatedKeywordBase);
  for (const issue of closingIssues) relatedIssues.delete(issue);
  ${bodyExtraction}
  ${deduplication}
  return {
    prs: [...prNumbers], closing: [...closingIssues], related: [...relatedIssues],
    referencedPRs: [...referencedPRs],
    duplicate: [
      hasReleaseNotification([{body: 'Released in [1.2.3]'}], '1.2.3'),
      hasReleaseNotification([{body: 'Related PR Released: [1.2.3]'}], '1.2.3'),
      hasReleaseNotification([{body: null}, {body: 'Released in [1.2.2]'}], '1.2.3')
    ]
  };
`);
const results = cases.map(c => ({name: c.name, ...parse(env.TARGET_OWNER, env.TARGET_REPO, c.release, c.body, 1)}));
const validate = new Function('context', 'process', `${guard} return 'allowed';`);
const guards = [];
for (const owner of [env.TARGET_OWNER, 'koxudaxi', 'another']) {
  // The guard logs skipped repositories; capture those through the process stdout as well.
  guards.push({owner, result: validate({repo: {owner, repo: env.TARGET_REPO}}, {env}) ?? 'skipped'});
}
guards.push({owner: env.TARGET_OWNER, repo: 'another', result: validate({repo: {owner: env.TARGET_OWNER, repo: 'another'}}, {env}) ?? 'skipped'});
try {
  validate({repo: {}}, {env: {}});
} catch (error) {
  guards.push({missing: error.message});
}
console.log(JSON.stringify({results, guards}, null, 2));
