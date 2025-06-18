import os
import requests
import argparse
import re
import json
import subprocess
import difflib
from openai import OpenAI
from colorama import init, Fore, Style
from pathlib import Path

# --- Initialize Colorama & Global Setup ---
init(autoreset=True)
if "OPENAI_API_KEY" not in os.environ:
    print(Fore.RED + "Error: Please set the OPENAI_API_KEY environment variable.")
    exit(1)
client = OpenAI()
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
HEADERS = {"Authorization": f"token {GITHUB_TOKEN}"} if GITHUB_TOKEN else {}

# --- Helper, NPM, and Changelog Functions (Unchanged) ---
def parse_package_json(file_path):
    try:
        with open(file_path, 'r') as f: data = json.load(f)
        dependencies = data.get("dependencies", {})
        dev_dependencies = data.get("devDependencies", {})
        all_dependencies = {**dependencies, **dev_dependencies}
        return all_dependencies
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(Fore.RED + f"Error reading package.json: {e}"); return None

def get_npm_package_info(package_name):
    url = f"https://registry.npmjs.org/{package_name}/latest"
    try:
        response = requests.get(url, timeout=10); response.raise_for_status(); return response.json()
    except requests.exceptions.RequestException: return None

def get_changelog(repo_url, repo_directory=None):
    if not repo_url or "github.com" not in repo_url: return None
    match = re.search(r"github\.com/([^/]+/[^/]+?)(\.git)?$", repo_url)
    if not match: return None
    repo_name = match.group(1)
    try:
        api_url = f"https://api.github.com/repos/{repo_name}"
        response = requests.get(api_url, headers=HEADERS, timeout=10); response.raise_for_status()
        default_branch = response.json().get("default_branch")
        changelog_filenames = ["CHANGELOG.md", "CHANGELOG", "changelog.md", "History.md", "NEWS.md"]
        search_paths = [repo_directory] if repo_directory and repo_directory != "." else [""]
        for path in search_paths:
            if path is None: continue
            for filename in changelog_filenames:
                full_path = f"{path}/{filename}" if path else filename
                raw_url = f"https://raw.githubusercontent.com/{repo_name}/{default_branch}/{full_path}"
                try:
                    res = requests.get(raw_url, timeout=10)
                    if res.status_code == 200: return res.text[:40000]
                except requests.exceptions.RequestException: continue
    except requests.exceptions.RequestException: pass
    releases_api_url = f"https://api.github.com/repos/{repo_name}/releases"
    try:
        response = requests.get(releases_api_url, headers=HEADERS, timeout=10); response.raise_for_status()
        releases_data = response.json()
        if not releases_data: return None
        synthetic_changelog = "".join([f"## Version: {r.get('tag_name', 'N/A')}\n\n{r.get('body')}\n\n---\n" for r in releases_data[:20] if r.get('body')])
        return synthetic_changelog if synthetic_changelog else None
    except requests.exceptions.RequestException: return None
    return None

# --- Main Analysis & File Scanning Functions ---
def summarize_and_classify_changes(package_name, changelog_content, missing_peers=None):
    print(Style.DIM + f"-> Performing high-level analysis for '{package_name}'...")
    system_prompt = (
        "You are an expert software engineer's assistant. Your task is to analyze a "
        "changelog and classify the update risk. First, on a new line, write 'RISK:', "
        "followed by ONE of the keywords: DANGEROUS, CAUTION, or SAFE. "
        "DANGEROUS means there are definite code-breaking changes. "
        "CAUTION means there are new features, deprecations, or potential issues like missing peer dependencies. "
        "SAFE means it's mostly bug fixes. "
        "If I provide a list of missing peer dependencies, the risk MUST be CAUTION or DANGEROUS, and you MUST mention them in the summary. "
        "Then, on a new line, provide a concise summary for a developer."
    )
    peer_context = ""
    if missing_peers:
        peer_context = f"CRITICAL CONTEXT: This update requires peer dependencies that are missing from the user's project: {', '.join(missing_peers.keys())}. You must mention this and flag the risk as CAUTION."
    user_prompt = (f"Analyze the changelog for '{package_name}'.\n{peer_context}\n\n"
                   f"Please classify the update risk and then provide your summary.\n\nChangelog:\n{changelog_content}")
    try:
        response = client.chat.completions.create(model="gpt-4o", messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}], temperature=0.1, max_tokens=500)
        content = response.choices[0].message.content.strip()
        lines = content.split('\n'); risk_line = lines[0]; summary = "\n".join(lines[1:]).strip()
        if missing_peers and "SAFE" in risk_line: risk = "CAUTION"
        elif "DANGEROUS" in risk_line: risk = "DANGEROUS"
        elif "CAUTION" in risk_line: risk = "CAUTION"
        else: risk = "SAFE"
        return risk, summary
    except Exception as e:
        return "UNKNOWN", f"An error occurred with the OpenAI API: {e}"

def find_relevant_files(src_dir, package_name):
    if not src_dir: return {}
    relevant_files = {}; src_path = Path(src_dir)
    if not src_path.is_dir(): return {}
    print(Style.DIM + f"-> Scanning '{src_dir}' for files using '{package_name}' (excluding node_modules)...")
    for ext in ["*.js", "*.jsx", "*.ts", "*.tsx"]:
        for file_path in src_path.rglob(ext):
            if "node_modules" in file_path.parts: continue
            try:
                content = file_path.read_text(encoding='utf-8')
                if re.search(fr'from ["\']{package_name}["\']|require\(["\']{package_name}["\']\)', content):
                    relevant_files[str(file_path)] = content
            except Exception: continue
    return relevant_files

def get_code_patches(package_name, changelog_content, relevant_files):
    print(Style.DIM + f"-> Performing deep scan for '{package_name}' on {len(relevant_files)} files...")
    system_prompt_analyze = (
        "You are an automated code refactoring tool. I will provide a changelog for a library update and a user's code file. "
        "Your task is to rewrite the entire code file to be compatible with the new version, applying any necessary breaking changes from the changelog. "
        "Respond with a JSON object containing a single key: 'new_content' (a string containing the complete, corrected file content). "
        "Make sure the response is only the JSON object and nothing else."
    )
    patched_files_report = []
    for file_path, content in relevant_files.items():
        print(Style.DIM + f"  - Generating patch for {os.path.basename(file_path)}...")
        user_prompt_analyze = (f"Changelog for {package_name}:\n{changelog_content}\n\n"
                               f"Rewrite the following code file to be compatible with the breaking changes described. Do not add any new functionality.\n"
                               f"Code File Path: {file_path}\nOriginal Code Content:\n```javascript\n{content}\n```")
        try:
            response = client.chat.completions.create(model="gpt-4o", response_format={"type": "json_object"}, messages=[{"role": "system", "content": system_prompt_analyze}, {"role": "user", "content": user_prompt_analyze}], temperature=0.0)
            analysis = json.loads(response.choices[0].message.content)
            if analysis.get("new_content") and analysis.get("new_content") != content:
                patched_files_report.append({"file": file_path, "original_content": content, "new_content": analysis["new_content"]})
        except Exception as e:
            print(Fore.YELLOW + f"Warning: Could not generate patch for {file_path}. {e}"); continue
    return patched_files_report

def apply_code_patches(patched_files, package_name):
    if not patched_files: return True

    print(Fore.CYAN + Style.BRIGHT + f"\nAI has generated code patches for '{package_name}'. Please review and approve each change.")
    successful_patches = 0

    for patch in patched_files:
        file_path, original, new = patch['file'], patch['original_content'], patch['new_content']
        print(Style.BRIGHT + f"\n--- Patch for: {Fore.YELLOW}{file_path} ---")
        diff = difflib.unified_diff(original.splitlines(keepends=True), new.splitlines(keepends=True), fromfile='original', tofile='proposed')
        for line in diff:
            if line.startswith('+'): print(Fore.GREEN + line, end="")
            elif line.startswith('-'): print(Fore.RED + line, end="")
            elif line.startswith('^'): print(Fore.BLUE + line, end="")
            else: print(Style.DIM + line, end="")

        try:
            proceed = input(Style.BRIGHT + "\nApply this patch? (y/n) ").lower() == 'y'
            if proceed:
                backup_path = file_path + ".bak"
                print(Style.DIM + f"-> Creating backup: {backup_path}")
                Path(file_path).rename(backup_path)
                print(Style.DIM + f"-> Writing new content to {file_path}")
                with open(file_path, 'w', encoding='utf-8') as f: f.write(new)
                print(Fore.GREEN + "✅ Patch applied successfully.")
                successful_patches += 1
            else:
                print(Fore.YELLOW + "-> Patch skipped.")
        except (EOFError, KeyboardInterrupt):
            print("\n\nUser cancelled patching process."); return False

    return successful_patches == len(patched_files)

def update_packages(packages_to_update, pkg_json_path, all_missing_peers):
    project_dir = os.path.dirname(pkg_json_path)
    print(Fore.CYAN + Style.BRIGHT + f"\nUpdating {len(packages_to_update)} approved packages in package.json...")
    try:
        with open(pkg_json_path, 'r') as f: pkg_data = json.load(f)
        if all_missing_peers:
            print(Fore.CYAN + f"-> Adding {len(all_missing_peers)} missing peer dependencies...")
            if "dependencies" not in pkg_data: pkg_data["dependencies"] = {}
            for peer, version in all_missing_peers.items():
                if peer not in pkg_data["dependencies"]:
                    pkg_data["dependencies"][peer] = version
                    print(f"  - Adding '{peer}@{version}' to dependencies.")
        for pkg in packages_to_update:
            pkg_name, new_version = pkg['name'], pkg['latest']
            for dep_type in ["dependencies", "devDependencies"]:
                if pkg_name in pkg_data.get(dep_type, {}):
                    old_version_str = pkg_data[dep_type].get(pkg_name, ""); prefix = re.match(r"^[~^]?", old_version_str).group(0) or ""
                    pkg_data[dep_type][pkg_name] = f"{prefix}{new_version}"
                    print(f"  - Updating '{pkg_name}' to version '{prefix}{new_version}'")
        with open(pkg_json_path, 'w') as f: json.dump(pkg_data, f, indent=2)
        print(Fore.CYAN + "✅ package.json has been updated.")
        use_yarn = os.path.exists(os.path.join(project_dir, "yarn.lock"))
        install_command = "yarn install" if use_yarn else "npm install"
        print(Fore.CYAN + f"-> Running '{install_command}' to apply all changes...")
        subprocess.run(install_command.split(), cwd=project_dir, check=True, capture_output=True, text=True)
        print(Fore.CYAN + "✅ Lock file has been updated successfully.")
        return True
    except (FileNotFoundError, json.JSONDecodeError, subprocess.CalledProcessError, FileNotFoundError) as e:
        print(Fore.RED + f"❌ Error during update process:\n{e}"); return False

# --- MAIN ORCHESTRATION ---
def main():
    parser = argparse.ArgumentParser(description="Analyze dependencies, auto-patch code, and update packages.", formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("file_path", type=str, help="The path to your package.json file.")
    parser.add_argument("--src", type=str, help="Path to your source code directory for deep scan analysis.")
    parser.add_argument("--apply-patches", action="store_true", help="Enable the experimental AI code patching feature after review.")
    parser.add_argument("--risk", type=str, default='SAFE', help="Comma-separated risk levels to attempt to update (e.g., SAFE,CAUTION).")
    parser.add_argument("--yes", "-y", action="store_true", help="Skip all confirmation prompts (DANGEROUS: auto-applies patches).")
    args = parser.parse_args()

    all_packages = parse_package_json(args.file_path)
    if not all_packages: return

    print(f"\nFound {len(all_packages)} total dependencies. Analyzing for updates...")
    outdated_packages = []
    for package_name, current_version_str in all_packages.items():
        if not isinstance(current_version_str, str) or not current_version_str: continue

        print(Style.BRIGHT + f"\n--- Checking '{package_name}' ---")
        current_version = re.sub(r"[^\d.]", "", current_version_str)
        latest_info = get_npm_package_info(package_name)
        if not latest_info: continue
        latest_version = latest_info.get("version")

        if latest_version and current_version and latest_version != current_version:
            print(f"-> Update found: {current_version} -> {latest_version}")
            missing_peers = {p: v for p, v in latest_info.get('peerDependencies', {}).items() if p not in all_packages}
            if missing_peers: print(Fore.YELLOW + f"-> Warning: Missing peer dependencies found: {', '.join(missing_peers.keys())}")

            changelog = get_changelog(latest_info.get("repository", {}).get("url"), latest_info.get("repository", {}).get("directory")) or "Could not retrieve changelog."
            risk, summary = summarize_and_classify_changes(package_name, changelog, missing_peers)

            patched_files = []
            if (risk == "DANGEROUS" or risk == "CAUTION") and args.src:
                relevant_files = find_relevant_files(args.src, package_name)
                if relevant_files:
                    patched_files = get_code_patches(package_name, changelog, relevant_files)

            if patched_files:
                print(Fore.YELLOW + f"-> Actionable code patches found. Promoting risk to DANGEROUS.")
                risk = "DANGEROUS"

            outdated_packages.append({
                "name": package_name, "current": current_version, "latest": latest_version,
                "risk": risk, "summary": summary, "patched_files": patched_files, "missing_peers": missing_peers
            })
        else:
            print(Fore.GREEN + "-> Up to date.")

    # --- Auto-updating and Final Report Logic ---
    auto_update_levels = [level.strip().upper() for level in args.risk.split(',')]
    packages_to_action = [p for p in outdated_packages if p['risk'] in auto_update_levels]
    packages_to_report = [p for p in outdated_packages if p['risk'] not in auto_update_levels]
    updated_packages_list = []

    if packages_to_action:
        print(Fore.CYAN + Style.BRIGHT + f"\n\nThe following packages match your risk level ('{args.risk}') and will be processed:")
        for pkg in packages_to_action:
            print(f"  - {pkg['name']} ({pkg['risk']}): {pkg['current']} -> {pkg['latest']}")
            if pkg['patched_files']:
                print(Fore.YELLOW + f"    (This update has {len(pkg['patched_files'])} AI-generated code patches to review)")

        proceed = args.yes or input(Style.BRIGHT + "\nDo you want to proceed with this action? (y/n) ").lower() == 'y'
        if proceed:
            # First, apply code patches for all approved packages if the flag is set
            if args.apply_patches:
                for pkg in packages_to_action:
                    apply_code_patches(pkg['patched_files'], pkg['name'])

            # Then, update package.json and install for all approved packages
            all_missing_peers_to_add = {p: v for pkg in packages_to_action for p, v in pkg['missing_peers'].items()}
            if update_packages(packages_to_action, args.file_path, all_missing_peers_to_add):
                updated_packages_list = packages_to_action
        else:
            print("Process cancelled. No files were changed.")
            packages_to_report.extend(packages_to_action)

    print("\n\n" + "="*28 + " Final Report " + "="*28)
    if updated_packages_list:
        print(Fore.GREEN + Style.BRIGHT + "\n✅ Packages Updated Successfully")
        print(Fore.GREEN + "--------------------------------\n")
        for pkg in updated_packages_list:
            print(f"{Style.BRIGHT}{pkg['name']}: {Fore.GREEN}{pkg['current']} -> {pkg['latest']}")
            if pkg['missing_peers']:
                print(Fore.GREEN + f"  - Also added peer(s): {', '.join(pkg['missing_peers'].keys())}")

    if packages_to_report:
        print(Fore.YELLOW + Style.BRIGHT + "\n⚠️ Manual Review Required")
        print(Fore.YELLOW + "---------------------------\n")
        risk_order = {"DANGEROUS": 0, "CAUTION": 1, "SAFE": 2, "UNKNOWN": 3}
        packages_to_report.sort(key=lambda p: risk_order.get(p["risk"], 3))
        for pkg in packages_to_report:
            risk = pkg["risk"]
            color = Fore.RED if risk == "DANGEROUS" else Fore.YELLOW if risk == "CAUTION" else Fore.WHITE
            risk_label = f"🚨 {risk}" if risk == "DANGEROUS" else f"⚠️ {risk}" if risk == "CAUTION" else f"❔ {risk}"
            print(f"\n{color}{Style.BRIGHT}{'-' * 70}")
            print(f"{color}{Style.BRIGHT}{pkg['name']}")
            print(f"{color}Update from {pkg['current']} -> {pkg['latest']}")
            print(f"{color}Risk Level: {risk_label}")
            print(color + "-" * 70)
            print(Style.RESET_ALL + pkg['summary'])
            if pkg.get('patched_files'):
                print(Style.BRIGHT + "\nProposed Patches (run with --apply-patches to apply):")
                for item in pkg['patched_files']:
                    print(f"  - {Fore.CYAN}{os.path.relpath(item['file'])}")

    if not updated_packages_list and not packages_to_report:
        print(Fore.GREEN + "\n✅ All dependencies are already up to date!")

if __name__ == "__main__":
    main()