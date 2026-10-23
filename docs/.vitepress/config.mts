import { posix } from 'node:path'
import { defineConfig } from 'vitepress'

const REPO = 'https://github.com/tyevco/jlcplace'

// Pages live in docs/. A relative link to anything that is not a page (a module, a test, the workflow, a folder)
// goes to that file on GitHub, so the same links work on GitHub and on the site.
function repoLinks(md: any) {
  const next = md.renderer.rules.link_open ??
    ((tokens: any, idx: number, options: any, _env: any, self: any) => self.renderToken(tokens, idx, options))
  md.renderer.rules.link_open = (tokens: any, idx: number, options: any, env: any, self: any) => {
    const token = tokens[idx]
    const href: string | null = token.attrGet('href')
    if (href && !/^([a-z][a-z0-9+.-]*:|#|\/)/i.test(href)) {
      const [path, hash] = href.split('#')
      const isPage = path === '' || /\.(md|html)$/i.test(path)
        || (!path.endsWith('/') && !/[./]/.test(posix.basename(path)))
      if (!isPage) {
        const target = posix.normalize(posix.join('docs', posix.dirname(env.relativePath ?? ''), path))
        const kind = path.endsWith('/') ? 'tree' : 'blob'
        token.attrSet('href', `${REPO}/${kind}/main/${target.replace(/\/$/, '')}${hash ? '#' + hash : ''}`)
      }
    }
    return next(tokens, idx, options, env, self)
  }
}

export default defineConfig({
  title: 'jlcplace',
  description: "Check and correct JLCPCB pick-and-place (CPL) files for KiCad boards by simulating JLC's placement "
    + 'with its own EasyEDA footprints.',
  base: '/jlcplace/',
  lang: 'en-US',
  cleanUrls: true,
  lastUpdated: true,
  srcExclude: ['**/node_modules/**', '**/README.md'],
  head: [['link', { rel: 'icon', href: '/jlcplace/favicon.svg', type: 'image/svg+xml' }]],
  markdown: {
    config: (md) => { md.use(repoLinks) },
  },
  themeConfig: {
    logo: '/favicon.svg',
    nav: [
      { text: 'Guide', link: '/guide/getting-started' },
      { text: 'Why parts end up misaligned', link: '/concepts/why' },
      { text: 'Reference', link: '/reference/cli' },
    ],
    sidebar: [
      {
        text: 'Guide',
        items: [
          { text: 'Getting started', link: '/guide/getting-started' },
          { text: 'Checking a CPL', link: '/guide/check' },
          { text: 'Writing a CPL', link: '/guide/cpl' },
          { text: "JLC's preview and confirm", link: '/guide/preview' },
          { text: 'The correction database', link: '/guide/database' },
          { text: 'Continuous integration', link: '/guide/ci' },
          { text: 'Troubleshooting', link: '/guide/troubleshooting' },
        ],
      },
      {
        text: 'Concepts',
        items: [
          { text: 'Why parts end up misaligned', link: '/concepts/why' },
          { text: 'How the check works', link: '/concepts/how-it-works' },
          { text: 'Verdicts and statuses', link: '/concepts/verdicts' },
        ],
      },
      {
        text: 'Reference',
        items: [
          { text: 'Commands', link: '/reference/cli' },
          { text: 'File formats', link: '/reference/files' },
          { text: 'Exit codes and environment', link: '/reference/environment' },
        ],
      },
      {
        text: 'About',
        items: [
          { text: 'Limits', link: '/about/limits' },
          { text: 'Data and licensing', link: '/about/licensing' },
          { text: 'Development', link: '/about/development' },
        ],
      },
    ],
    outline: { level: [2, 3] },
    search: { provider: 'local' },
    socialLinks: [{ icon: 'github', link: REPO }],
    editLink: { pattern: `${REPO}/edit/main/docs/:path`, text: 'Edit this page on GitHub' },
    footer: {
      message: 'MIT licensed. EasyEDA footprint data belongs to the JLCEDA/EasyEDA Official Library '
        + '(<a href="https://lceda.cn/">lceda.cn</a>, <a href="https://easyeda.com">easyeda.com</a>).',
    },
  },
})
