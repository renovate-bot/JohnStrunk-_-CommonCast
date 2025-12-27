# CommonCast

License: LGPL-3.0-only

## New project checklist

- [ ] Adjust the Mergify configuration to customize the merge conditions
  - Consider enabling [Merge
    protections](https://docs.mergify.com/merge-protections/) to enable
    `Depends-On: <PR#>`, `Merge-After: <ISO 8601>`.
  - Consider enforcing the merge queue (only allowing Mergify to merge PRs) by
    [protecting the default
    branch](https://dashboard.mergify.com/queues/deployment/enforcement).
- [ ] Update the README with project-specific information
- [ ] Update [Copilot setup
  workflow](./template/.github/workflows/copilot-setup-steps.yaml) with
  project-specific tools
