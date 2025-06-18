# 🧠 Project Doctor: Intelligent Dependency Analyzer & Auto-Patcher

This CLI tool helps Node.js developers analyze and update dependencies in their `package.json` intelligently using the OpenAI API. It automatically checks for outdated packages, evaluates changelogs to assess **update risk** (e.g., `SAFE`, `CAUTION`, `DANGEROUS`), identifies missing peer dependencies, and can even **auto-generate and apply code patches** when breaking changes are detected.

---

## ✨ Features

- 📦 Detect outdated dependencies (`dependencies` and `devDependencies`)
- 🧠 Use GPT-4o to analyze changelogs and classify update risk
- 🔧 Optionally generate and apply AI-assisted code patches for breaking changes
- ⚠️ Warn about and optionally auto-add missing peer dependencies
- 🔍 Deep scan your source code for usage of updated packages
- 📝 Interactive diffs and patch approval flow
- ✅ Auto-update `package.json` and reinstall packages

---

## 🔧 Requirements

- Python 3.8+
- OpenAI API Key (`OPENAI_API_KEY`)
- (Optional) GitHub Token (`GITHUB_TOKEN`) — for better changelog access

### Install dependencies

```bash
pip install -r requirements.txt
```

Example `requirements.txt`:
```txt
openai
colorama
requests
```

---

## 🚀 Usage

```bash
python project_doctor.py path/to/package.json [options]
```

### Required Arguments

- `file_path`: Path to your `package.json`.

### Optional Flags

| Option               | Description |
|----------------------|-------------|
| `--src`              | Path to your source code directory for scanning (`.js`, `.jsx`, `.ts`, `.tsx`) |
| `--apply-patches`    | Apply AI-generated code patches (requires `--src`) |
| `--risk SAFE,CAUTION`| Comma-separated risk levels to auto-update (`SAFE`, `CAUTION`, `DANGEROUS`) |
| `--yes` or `-y`      | Skip confirmation prompts (dangerous!) |

---

## 🔐 Environment Variables

Set the following in your shell or `.env` file:

```bash
export OPENAI_API_KEY="sk-..."
export GITHUB_TOKEN="ghp_..."  # Optional
```

---

## 🧪 Example

```bash
python project_doctor.py ./package.json --src ./src --apply-patches --risk SAFE,CAUTION
```

- Scans dependencies in `package.json`
- Analyzes changelogs via GPT
- Classifies and summarizes risk
- Finds source files that use each outdated package
- Uses GPT to rewrite files (if risky updates)
- Applies patches and updates your project

---

## 📘 Risk Classification Guide

| Risk Level | Meaning |
|------------|---------|
| `SAFE`     | Bug fixes only, backward-compatible |
| `CAUTION`  | New features, potential breakage, missing peers |
| `DANGEROUS`| Known breaking changes; code updates required |

---

## 🛡️ Safety and Backups

Before applying patches:
- A `.bak` backup is created
- Interactive diff is shown
- You confirm each patch

Use `--yes` for full automation (only recommended in CI/CD with safe risk level).

---

## 🤖 Powered By

- [OpenAI GPT-4o](https://openai.com/)
- [NPM Registry](https://registry.npmjs.org/)
- [GitHub API](https://docs.github.com/en/rest)
- Python's `colorama`, `requests`, `argparse`, and `subprocess`

---

## 🧹 TODO

- Add unit tests
- Support `pnpm` or lock file diffing
- Integrate with CI systems
- Export audit report to Markdown or HTML

---

## 📄 License

MIT © Andrew Saldana