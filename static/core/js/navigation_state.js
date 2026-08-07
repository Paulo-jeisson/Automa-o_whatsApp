(() => {
  if (window.IAAtendeNavigation) return;

  const body = document.body;
  const enabled = body?.dataset.authenticated === 'true';
  const identity = `${body?.dataset.scrollUser || 'anonymous'}:${body?.dataset.scrollTenant || 'none'}`;
  const storageKey = `iaatende:navigation-scroll:pending:${identity}`;
  const maxAgeMs = 2 * 60 * 1000;

  const containerSelectors = element => {
    const owner = element?.closest?.('[data-scroll-containers]');
    return (owner?.dataset.scrollContainers || '')
      .split(',').map(value => value.trim()).filter(Boolean);
  };

  const capture = ({containers = []} = {}) => ({
    path: window.location.pathname,
    timestamp: Date.now(),
    page: window.scrollY,
    containers: containers.map(selector => {
      const element = document.querySelector(selector);
      return element ? {selector, top: element.scrollTop, left: element.scrollLeft} : null;
    }).filter(Boolean),
  });

  const applyRestore = state => {
    if (!state || state.path !== window.location.pathname) return;
    window.scrollTo(0, Number(state.page) || 0);
    (state.containers || []).forEach(item => {
      const element = document.querySelector(item.selector);
      if (!element) return;
      element.scrollTop = Number(item.top) || 0;
      element.scrollLeft = Number(item.left) || 0;
    });
  };

  const restore = (state, {immediate = false} = {}) => {
    if (immediate) {
      applyRestore(state);
      return;
    }
    requestAnimationFrame(() => applyRestore(state));
  };

  const save = state => {
    if (!enabled || !state) return;
    try { sessionStorage.setItem(storageKey, JSON.stringify(state)); } catch (_error) {}
  };

  const consume = () => {
    if (!enabled) return null;
    let state = null;
    try {
      const raw = sessionStorage.getItem(storageKey);
      sessionStorage.removeItem(storageKey);
      if (raw) state = JSON.parse(raw);
    } catch (_error) {}
    if (!state || Date.now() - Number(state.timestamp || 0) > maxAgeMs) return null;
    return state.path === window.location.pathname ? state : null;
  };

  const saveForElement = element => save(capture({containers: containerSelectors(element)}));

  if (enabled) {
    document.addEventListener('submit', event => {
      const form = event.target.closest('#system-page-content form');
      if (!form) return;
      queueMicrotask(() => {
        if (event.defaultPrevented || form.target === '_blank' || form.hasAttribute('data-scroll-reset')) return;
        const action = new URL(form.action || window.location.href, window.location.href);
        if (action.origin !== window.location.origin || action.pathname.includes('/logout/')) return;
        saveForElement(form);
      });
    });

    document.addEventListener('click', event => {
      const link = event.target.closest('#system-page-content a[href]');
      if (!link || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
      queueMicrotask(() => {
        if (event.defaultPrevented || link.target === '_blank' || link.hasAttribute('download') || link.hasAttribute('data-scroll-reset')) return;
        const target = new URL(link.href, window.location.href);
        const samePageNavigation = (
          target.origin === window.location.origin
          && target.pathname === window.location.pathname
          && target.search !== window.location.search
        );
        if (samePageNavigation) saveForElement(link);
      });
    });
  }

  window.IAAtendeNavigation = {
    capture,
    restore,
    save,
    saveCurrent: element => saveForElement(element || document.querySelector('#system-page-content')),
  };
  const pendingState = window.__iaAtendePendingNavigationState || consume();
  try {
    restore(pendingState, {immediate: true});
  } finally {
    document.documentElement.classList.remove('ia-scroll-restoring');
    delete window.__iaAtendePendingNavigationState;
  }
})();
