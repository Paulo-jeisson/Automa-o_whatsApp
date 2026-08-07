from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase


class NavigationStateJavascriptTests(SimpleTestCase):
    @classmethod
    def source(cls):
        return (Path(settings.BASE_DIR) / 'static/core/js/navigation_state.js').read_text(
            encoding='utf-8',
        )

    def test_state_is_tenant_scoped_short_lived_and_consumed_once(self):
        source = self.source()
        self.assertIn("dataset.scrollUser", source)
        self.assertIn("dataset.scrollTenant", source)
        self.assertIn('2 * 60 * 1000', source)
        self.assertIn('sessionStorage.removeItem(storageKey)', source)
        self.assertIn("state.path === window.location.pathname", source)

    def test_only_safe_internal_interactions_are_tracked(self):
        source = self.source()
        self.assertIn("#system-page-content form", source)
        self.assertIn("#system-page-content a[href]", source)
        self.assertIn("target.pathname === window.location.pathname", source)
        self.assertIn("action.pathname.includes('/logout/')", source)
        self.assertIn("form.target === '_blank'", source)
        self.assertIn("link.hasAttribute('download')", source)
        self.assertIn("data-scroll-reset", source)

    def test_restore_uses_dynamic_page_and_selected_container_positions(self):
        source = self.source()
        self.assertIn('window.scrollY', source)
        self.assertIn('window.scrollTo(0, Number(state.page) || 0)', source)
        self.assertIn('element.scrollTop = Number(item.top) || 0', source)
        self.assertIn('restore(pendingState, {immediate: true})', source)
        self.assertIn("classList.remove('ia-scroll-restoring')", source)
        self.assertNotIn('setTimeout(', source)
        self.assertNotIn('history.scrollRestoration', source)
        self.assertNotIn('scrollTo(0, 1200)', source)
