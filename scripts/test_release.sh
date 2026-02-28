#!/bin/bash
# Release test script for BLSync v0.4.2
# This script validates the release by running tests and checking version consistency

set -e

VERSION="0.4.2"
RELEASE_COLOR='\033[0;32m'
ERROR_COLOR='\033[0;31m'
INFO_COLOR='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${INFO_COLOR}========================================${NC}"
echo -e "${INFO_COLOR}BLSync v${VERSION} Release Test${NC}"
echo -e "${INFO_COLOR}========================================${NC}"

# Function to print colored output
print_success() {
    echo -e "${RELEASE_COLOR}✓ $1${NC}"
}

print_error() {
    echo -e "${ERROR_COLOR}✗ $1${NC}"
}

print_info() {
    echo -e "${INFO_COLOR}ℹ $1${NC}"
}

# Test 1: Check version in pyproject.toml
print_info "Checking version in pyproject.toml..."
if grep -q "version = \"${VERSION}\"" pyproject.toml; then
    print_success "pyproject.toml version is correct"
else
    print_error "pyproject.toml version mismatch"
    exit 1
fi

# Test 2: Check CHANGELOG is updated
print_info "Checking CHANGELOG for v${VERSION}..."
if grep -q "## \[${VERSION}\]" CHANGELOG.md; then
    print_success "CHANGELOG contains v${VERSION} entry"
else
    print_error "CHANGELOG missing v${VERSION} entry"
    exit 1
fi

# Test 3: Check CHANGELOG links
print_info "Checking CHANGELOG version links..."
if grep -q "\[${VERSION}\]: " CHANGELOG.md; then
    print_success "CHANGELOG contains version link"
else
    print_error "CHANGELOG missing version link"
    exit 1
fi

# Test 4: Run Python tests
print_info "Running Python tests..."
if uv run pytest tests/ -v --tb=short; then
    print_success "All Python tests passed"
else
    print_error "Python tests failed"
    exit 1
fi

# Test 5: Check code formatting
print_info "Checking code formatting with ruff..."
if uv run ruff check .; then
    print_success "Code formatting check passed"
else
    print_error "Code formatting issues found"
    exit 1
fi

# Test 6: Verify package can be built
print_info "Testing package build..."
TEMP_DIR=$(mktemp -d)
cd "${TEMP_DIR}"
if uv build --outdir /tmp/blsync-build-test /Users/beny/dev_src/BLSync > /dev/null 2>&1; then
    print_success "Package builds successfully"
    rm -rf /tmp/blsync-build-test
else
    print_error "Package build failed"
    cd /Users/beny/dev_src/BLSync
    rm -rf "${TEMP_DIR}"
    exit 1
fi
cd /Users/beny/dev_src/BLSync
rm -rf "${TEMP_DIR}"

# Test 7: Check static files exist
print_info "Checking static frontend files..."
if [ -d "static" ] && [ -f "static/index.html" ]; then
    print_success "Static frontend files exist"
else
    print_error "Static frontend files missing"
    exit 1
fi

# Test 8: Verify git status (should be clean except for version changes)
print_info "Checking git status..."
GIT_CHANGES=$(git status --porcelain | grep -v "pyproject.toml" | grep -v "CHANGELOG.md" | grep -v "scripts/" || true)
if [ -z "$GIT_CHANGES" ]; then
    print_success "No unexpected changes detected"
else
    print_info "Note: There are changes besides version files:"
    git status --short
fi

echo ""
echo -e "${RELEASE_COLOR}========================================${NC}"
echo -e "${RELEASE_COLOR}All release tests passed! ✓${NC}"
echo -e "${RELEASE_COLOR}========================================${NC}"
echo ""
echo "Version: ${VERSION}"
echo "Date: $(date +%Y-%m-%d)"
echo ""
echo "Ready to commit and tag:"
echo "  git add pyproject.toml CHANGELOG.md"
echo "  git commit -m 'chore: release v${VERSION}'"
echo "  git tag -a v${VERSION} -m 'Release v${VERSION}'"
echo "  git push origin dev"
echo "  git push origin v${VERSION}"
