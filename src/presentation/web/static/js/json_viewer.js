/**
 * Interactive, collapsible JSON viewer for ctxins Web UI.
 * Provides hierarchical tree formatting, folding/expansion, syntax coloring,
 * key/value filtering, raw view toggling, and clipboard copying.
 */
class JsonViewer {
  /**
   * @param {Object} options
   * @param {HTMLElement} options.treeContainer
   * @param {HTMLElement} options.rawContainer
   * @param {HTMLElement} options.toolbar
   * @param {HTMLButtonElement} options.expandAllBtn
   * @param {HTMLButtonElement} options.collapseAllBtn
   * @param {HTMLButtonElement} options.viewTreeBtn
   * @param {HTMLButtonElement} options.viewRawBtn
   * @param {HTMLButtonElement} options.copyBtn
   * @param {HTMLInputElement} options.searchInput
   * @param {HTMLElement} options.searchMatches
   * @param {HTMLElement} options.typeBadge
   */
  constructor(options = {}) {
    this.treeContainer = options.treeContainer || null;
    this.rawContainer = options.rawContainer || null;
    this.toolbar = options.toolbar || null;
    this.expandAllBtn = options.expandAllBtn || null;
    this.collapseAllBtn = options.collapseAllBtn || null;
    this.viewTreeBtn = options.viewTreeBtn || null;
    this.viewRawBtn = options.viewRawBtn || null;
    this.copyBtn = options.copyBtn || null;
    this.searchInput = options.searchInput || null;
    this.searchMatches = options.searchMatches || null;
    this.typeBadge = options.typeBadge || null;

    this.currentData = null;
    this.rawText = '';
    this.isJson = false;
    this.viewMode = 'tree'; // 'tree' | 'raw'

    this._bindEvents();
  }

  _bindEvents() {
    if (this.expandAllBtn) {
      this.expandAllBtn.addEventListener('click', () => this.expandAll());
    }

    if (this.collapseAllBtn) {
      this.collapseAllBtn.addEventListener('click', () => this.collapseAll());
    }

    if (this.viewTreeBtn) {
      this.viewTreeBtn.addEventListener('click', () => this.setViewMode('tree'));
    }

    if (this.viewRawBtn) {
      this.viewRawBtn.addEventListener('click', () => this.setViewMode('raw'));
    }

    if (this.copyBtn) {
      this.copyBtn.addEventListener('click', () => this.copyToClipboard());
    }

    if (this.searchInput) {
      this.searchInput.addEventListener('input', (e) => this.filter(e.target.value));
    }
  }

  /**
   * Parse content and render either interactive JSON tree or formatted raw text.
   * @param {*} content
   */
  render(content) {
    if (this.searchInput) {
      this.searchInput.value = '';
    }
    if (this.searchMatches) {
      this.searchMatches.textContent = '';
      this.searchMatches.style.display = 'none';
    }

    const { isJson, data, rawText } = this._parseContent(content);
    this.isJson = isJson;
    this.currentData = data;
    this.rawText = rawText;

    if (this.typeBadge) {
      this.typeBadge.textContent = isJson ? 'JSON' : 'TEXT';
      this.typeBadge.className = isJson ? 'badge badge-info' : 'badge badge-secondary';
    }

    if (isJson) {
      if (this.expandAllBtn) this.expandAllBtn.style.display = '';
      if (this.collapseAllBtn) this.collapseAllBtn.style.display = '';
      if (this.viewTreeBtn && this.viewTreeBtn.parentElement) {
        this.viewTreeBtn.parentElement.style.display = 'inline-flex';
      }
      if (this.searchInput) this.searchInput.style.display = '';

      // Build interactive tree
      if (this.treeContainer) {
        this.treeContainer.innerHTML = '';
        const rootNode = this._createNode(null, data, false, 0);
        this.treeContainer.appendChild(rootNode);
      }

      // Build raw pre
      if (this.rawContainer) {
        this.rawContainer.textContent = rawText;
      }

      this.setViewMode('tree');
    } else {
      // Non-JSON content
      if (this.expandAllBtn) this.expandAllBtn.style.display = 'none';
      if (this.collapseAllBtn) this.collapseAllBtn.style.display = 'none';
      if (this.viewTreeBtn && this.viewTreeBtn.parentElement) {
        this.viewTreeBtn.parentElement.style.display = 'none';
      }
      if (this.searchInput) this.searchInput.style.display = 'none';

      if (this.rawContainer) {
        this.rawContainer.textContent = rawText;
      }
      this.setViewMode('raw');
    }
  }

  /**
   * Switch between Tree and Raw view modes.
   * @param {'tree'|'raw'} mode
   */
  setViewMode(mode) {
    this.viewMode = mode;
    if (mode === 'tree' && this.isJson) {
      if (this.treeContainer) this.treeContainer.style.display = 'block';
      if (this.rawContainer) this.rawContainer.style.display = 'none';
      if (this.viewTreeBtn) this.viewTreeBtn.classList.add('active');
      if (this.viewRawBtn) this.viewRawBtn.classList.remove('active');
      if (this.expandAllBtn) this.expandAllBtn.disabled = false;
      if (this.collapseAllBtn) this.collapseAllBtn.disabled = false;
      if (this.searchInput) this.searchInput.disabled = false;
    } else {
      if (this.treeContainer) this.treeContainer.style.display = 'none';
      if (this.rawContainer) this.rawContainer.style.display = 'block';
      if (this.viewTreeBtn) this.viewTreeBtn.classList.remove('active');
      if (this.viewRawBtn) this.viewRawBtn.classList.add('active');
      if (this.expandAllBtn) this.expandAllBtn.disabled = true;
      if (this.collapseAllBtn) this.collapseAllBtn.disabled = true;
      if (this.searchInput) this.searchInput.disabled = true;
    }
  }

  /**
   * Detect and parse JSON if present.
   * @private
   */
  _parseContent(content) {
    if (content === null || content === undefined) {
      return { isJson: false, data: null, rawText: '' };
    }

    if (typeof content === 'object') {
      try {
        const rawText = JSON.stringify(content, null, 2);
        return { isJson: true, data: content, rawText };
      } catch (_) {
        return { isJson: false, data: null, rawText: String(content) };
      }
    }

    if (typeof content === 'string') {
      const trimmed = content.trim();
      if (
        (trimmed.startsWith('{') && trimmed.endsWith('}')) ||
        (trimmed.startsWith('[') && trimmed.endsWith(']'))
      ) {
        try {
          const parsed = JSON.parse(trimmed);
          if (parsed && typeof parsed === 'object') {
            return {
              isJson: true,
              data: parsed,
              rawText: JSON.stringify(parsed, null, 2),
            };
          }
        } catch (_) {}
      }
      return { isJson: false, data: null, rawText: content };
    }

    return { isJson: false, data: null, rawText: String(content) };
  }

  /**
   * Create an interactive DOM node for a key-value or array item.
   * @private
   */
  _createNode(key, value, isLast, depth) {
    const nodeEl = document.createElement('div');
    nodeEl.className = 'json-node';

    const isObject = value !== null && typeof value === 'object' && !Array.isArray(value);
    const isArray = Array.isArray(value);
    const isComplex = isObject || isArray;

    if (!isComplex) {
      // Primitive line
      const row = document.createElement('div');
      row.className = 'json-node-row json-primitive-row';

      // Indent spacer matching caret
      const spacer = document.createElement('span');
      spacer.className = 'json-spacer';
      row.appendChild(spacer);

      if (key !== null) {
        const keySpan = document.createElement('span');
        keySpan.className = 'json-key';
        keySpan.textContent = `"${key}"`;
        row.appendChild(keySpan);

        const colon = document.createElement('span');
        colon.className = 'json-colon';
        colon.textContent = ': ';
        row.appendChild(colon);
      }

      const valSpan = document.createElement('span');
      valSpan.className = `json-val ${this._getPrimitiveClass(value)}`;
      valSpan.textContent = this._formatPrimitive(value);
      row.appendChild(valSpan);

      if (!isLast) {
        const comma = document.createElement('span');
        comma.className = 'json-comma';
        comma.textContent = ',';
        row.appendChild(comma);
      }

      nodeEl.appendChild(row);
      return nodeEl;
    }

    // Complex (Object or Array)
    const itemsCount = isArray ? value.length : Object.keys(value).length;
    const openBracketChar = isArray ? '[' : '{';
    const closeBracketChar = isArray ? ']' : '}';

    // Header row
    const headerRow = document.createElement('div');
    headerRow.className = 'json-node-row json-complex-header';

    const caret = document.createElement('span');
    caret.className = 'json-caret';
    caret.textContent = '▼';
    headerRow.appendChild(caret);

    if (key !== null) {
      const keySpan = document.createElement('span');
      keySpan.className = 'json-key';
      keySpan.textContent = `"${key}"`;
      headerRow.appendChild(keySpan);

      const colon = document.createElement('span');
      colon.className = 'json-colon';
      colon.textContent = ': ';
      headerRow.appendChild(colon);
    }

    const openBracket = document.createElement('span');
    openBracket.className = 'json-bracket';
    openBracket.textContent = openBracketChar;
    headerRow.appendChild(openBracket);

    // Collapsed preview pill
    const preview = document.createElement('span');
    preview.className = 'json-preview';
    preview.textContent = isArray
      ? `${itemsCount} item${itemsCount === 1 ? '' : 's'}`
      : `${itemsCount} key${itemsCount === 1 ? '' : 's'}`;
    preview.style.display = 'none';
    headerRow.appendChild(preview);

    // Collapsed close bracket
    const collapsedCloseBracket = document.createElement('span');
    collapsedCloseBracket.className = 'json-bracket json-bracket-collapsed';
    collapsedCloseBracket.textContent = closeBracketChar + (!isLast ? ',' : '');
    collapsedCloseBracket.style.display = 'none';
    headerRow.appendChild(collapsedCloseBracket);

    // Subtle hint on hover
    const hint = document.createElement('span');
    hint.className = 'json-meta-hint';
    hint.textContent = isArray
      ? ` (${itemsCount} items)`
      : ` (${itemsCount} keys)`;
    headerRow.appendChild(hint);

    nodeEl.appendChild(headerRow);

    // Children container
    const childrenContainer = document.createElement('div');
    childrenContainer.className = 'json-children';

    if (isArray) {
      value.forEach((item, idx) => {
        const childNode = this._createNode(
          null,
          item,
          idx === value.length - 1,
          depth + 1
        );
        childrenContainer.appendChild(childNode);
      });
    } else {
      const entries = Object.entries(value);
      entries.forEach(([childKey, childVal], idx) => {
        const childNode = this._createNode(
          childKey,
          childVal,
          idx === entries.length - 1,
          depth + 1
        );
        childrenContainer.appendChild(childNode);
      });
    }

    nodeEl.appendChild(childrenContainer);

    // Footer row (closing bracket)
    const footerRow = document.createElement('div');
    footerRow.className = 'json-node-row json-complex-footer';

    const footerSpacer = document.createElement('span');
    footerSpacer.className = 'json-spacer';
    footerRow.appendChild(footerSpacer);

    const closeBracket = document.createElement('span');
    closeBracket.className = 'json-bracket json-bracket-footer';
    closeBracket.textContent = closeBracketChar + (!isLast ? ',' : '');
    footerRow.appendChild(closeBracket);

    nodeEl.appendChild(footerRow);

    // Toggle collapse handler
    const toggleCollapse = (forceState) => {
      const isCurrentlyCollapsed = childrenContainer.classList.contains('collapsed');
      const shouldCollapse = forceState !== undefined ? forceState : !isCurrentlyCollapsed;

      if (shouldCollapse) {
        childrenContainer.classList.add('collapsed');
        caret.classList.add('collapsed');
        caret.textContent = '▶';
        preview.style.display = 'inline-block';
        collapsedCloseBracket.style.display = 'inline';
        footerRow.style.display = 'none';
        hint.style.display = 'none';
      } else {
        childrenContainer.classList.remove('collapsed');
        caret.classList.remove('collapsed');
        caret.textContent = '▼';
        preview.style.display = 'none';
        collapsedCloseBracket.style.display = 'none';
        footerRow.style.display = '';
        hint.style.display = '';
      }
    };

    headerRow.addEventListener('click', (e) => {
      e.stopPropagation();
      toggleCollapse();
    });

    nodeEl._toggleCollapse = toggleCollapse;

    // Default collapse level: auto-collapse deeper than depth 2 if very large
    if (depth >= 3 && itemsCount > 0) {
      toggleCollapse(true);
    }

    return nodeEl;
  }

  _getPrimitiveClass(val) {
    if (val === null) return 'json-null';
    if (typeof val === 'boolean') return 'json-boolean';
    if (typeof val === 'number') return 'json-number';
    return 'json-string';
  }

  _formatPrimitive(val) {
    if (val === null) return 'null';
    if (typeof val === 'boolean') return val ? 'true' : 'false';
    if (typeof val === 'number') return String(val);
    return JSON.stringify(val);
  }

  expandAll() {
    if (!this.treeContainer) return;
    const nodes = this.treeContainer.querySelectorAll('.json-node');
    nodes.forEach((node) => {
      if (typeof node._toggleCollapse === 'function') {
        node._toggleCollapse(false);
      }
    });
  }

  collapseAll() {
    if (!this.treeContainer) return;
    const nodes = this.treeContainer.querySelectorAll('.json-node');
    nodes.forEach((node) => {
      if (typeof node._toggleCollapse === 'function') {
        node._toggleCollapse(true);
      }
    });
    // Keep root node expanded so user sees top-level structure
    const rootNode = this.treeContainer.firstElementChild;
    if (rootNode && typeof rootNode._toggleCollapse === 'function') {
      rootNode._toggleCollapse(false);
    }
  }

  /**
   * Filter tree or highlight matching keys and values.
   * @param {string} query
   */
  filter(query) {
    if (!this.treeContainer) return;
    const q = (query || '').trim().toLowerCase();

    // Clear previous marks
    const marks = this.treeContainer.querySelectorAll('mark.json-highlight');
    marks.forEach((m) => {
      const parent = m.parentNode;
      if (parent) {
        parent.replaceChild(document.createTextNode(m.textContent || ''), m);
        parent.normalize();
      }
    });

    // Reset dimmed styling
    const allRows = this.treeContainer.querySelectorAll('.json-node-row');
    allRows.forEach((r) => r.classList.remove('json-matched', 'json-dimmed'));

    if (!q) {
      if (this.searchMatches) {
        this.searchMatches.textContent = '';
        this.searchMatches.style.display = 'none';
      }
      return;
    }

    let matchCount = 0;
    const targets = this.treeContainer.querySelectorAll('.json-key, .json-val');

    targets.forEach((el) => {
      const text = el.textContent || '';
      const lower = text.toLowerCase();
      const matchIdx = lower.indexOf(q);

      if (matchIdx >= 0) {
        matchCount++;
        const row = el.closest('.json-node-row');
        if (row) row.classList.add('json-matched');

        // Highlight matching text
        const before = text.substring(0, matchIdx);
        const match = text.substring(matchIdx, matchIdx + q.length);
        const after = text.substring(matchIdx + q.length);

        el.innerHTML = '';
        if (before) el.appendChild(document.createTextNode(before));
        const mark = document.createElement('mark');
        mark.className = 'json-highlight';
        mark.textContent = match;
        el.appendChild(mark);
        if (after) el.appendChild(document.createTextNode(after));

        // Auto-expand all parent nodes so match is visible
        let parent = el.closest('.json-node');
        while (parent && parent !== this.treeContainer) {
          if (typeof parent._toggleCollapse === 'function') {
            parent._toggleCollapse(false);
          }
          parent = parent.parentElement ? parent.parentElement.closest('.json-node') : null;
        }
      }
    });

    if (this.searchMatches) {
      this.searchMatches.textContent = `${matchCount} match${matchCount === 1 ? '' : 'es'}`;
      this.searchMatches.style.display = matchCount > 0 ? 'inline-block' : 'inline-block';
      this.searchMatches.className =
        matchCount > 0 ? 'modal-search-matches match-found' : 'modal-search-matches no-match';
    }
  }

  async copyToClipboard() {
    const textToCopy = this.rawText;
    if (!textToCopy) return;

    try {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        await navigator.clipboard.writeText(textToCopy);
      } else {
        const textarea = document.createElement('textarea');
        textarea.value = textToCopy;
        textarea.style.position = 'fixed';
        textarea.style.opacity = '0';
        document.body.appendChild(textarea);
        textarea.select();
        document.execCommand('copy');
        document.body.removeChild(textarea);
      }

      if (this.copyBtn) {
        const originalText = this.copyBtn.textContent;
        this.copyBtn.textContent = '✓ Copied!';
        this.copyBtn.classList.add('btn-success');
        setTimeout(() => {
          this.copyBtn.textContent = originalText;
          this.copyBtn.classList.remove('btn-success');
        }, 1500);
      }
    } catch (err) {
      console.error('[JsonViewer] Failed to copy to clipboard:', err);
    }
  }
}

window.JsonViewer = JsonViewer;
