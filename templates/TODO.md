# TODO

**⚠️ Warning:** This file syncs with GitHub Issues. Only add items you actually want to sync!

Follow the format below to ensure items parse correctly.

## Open

<!-- Add new items here as checkboxes -->
<!-- Format: - [ ] Your task description -->

## Done

<!-- Completed items go here. Mark with [x] instead of [ ] -->
<!-- Format: - [x] Your completed task description -->

## Reference

### How to Add Todos

**Basic format for new items:**
```
- [ ] Your task description
```

**After creating a GitHub issue**, link it like this:
```
- [ ] Your task description <!-- issue:123 -->
```

Replace `123` with your actual GitHub issue number.

**Completed items** (checked off):
```
- [x] Your completed task description
```

**With description and subtasks:**
```
- [ ] Your task description <!-- issue:123 -->
  > Add context about this task here. Can be multiple lines.
  - [ ] First subtask or criterion
  - [ ] Second subtask or criterion
```

Descriptions use indented `> ` lines, and subtasks use indented `- [ ]` checkboxes.

**With labels:**
```
- [ ] Your task description <!-- issue:123 -->
  > Task context
  labels: bug, priority-high, backend
  - [ ] First subtask
```

Labels use `labels: comma, separated, list` format on an indented line.

**CLI Commands:**
- `todo-sync comment <issue-id> "<message>"` — Add a comment to an issue
- `todo-sync assign <issue-id>` — Assign an issue to yourself

### Advanced Format: Descriptions and Subtasks

Items can include descriptions and subtasks for more context:

```
## Open
- [ ] Implement user authentication <!-- issue:1 -->
  > Allow users to sign in via OAuth2. Priority: High
  labels: feature, backend, authentication
  - [ ] Add OAuth2 integration
  - [ ] Create session management
  - [x] Design login UI

- [ ] Fix login bug <!-- issue:2 -->
  > Users cannot log in on Safari. Regression from v2.3.
  labels: bug, high-priority, regression
  - [ ] Reproduce on Safari 17
  - [ ] Check auth token expiry
  - [ ] Write regression test

## Done
- [x] Setup project repository
- [x] Create initial documentation
```

### Example Format (Reference Only - Simple Format)

For simple todos without descriptions or subtasks:

```
## Open
- [ ] Implement user authentication
- [ ] Add dark mode support
- [ ] Fix login bug

## Done
- [x] Setup project repository
- [x] Create initial documentation
```

**Important:** Only add items to ## Open and ## Done that you actually want to sync to GitHub!

### Important Rules

- ✅ Use `- [ ]` for unchecked items
- ✅ Use `- [x]` for completed items
- ✅ Sections must be `## Open` and `## Done` (with capital O and D)
- ✅ Issue links use `<!-- issue:NUMBER -->` format
- ✅ Labels use `labels: comma, separated, list` format (optional)
- ⚠️ Don't modify the section headers
- ⚠️ Don't change the checkbox format
- ⚠️ Keep issue numbers accurate when syncing
