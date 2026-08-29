import type { APIRoute } from 'astro';
import noticias from '../data/noticias.json';

// Google News Sitemap — só artigos dos últimos 2 dias
const SITE_URL  = 'https://noticiajuridicas.com.br';
const SITE_NAME = 'Notícia Jurídica';

export const GET: APIRoute = () => {
  const limite = Date.now() - 2 * 24 * 60 * 60 * 1000; // 48h atrás

  const recentes = noticias
    .filter(n => new Date(n.data).getTime() > limite)
    .sort((a, b) => new Date(b.data).getTime() - new Date(a.data).getTime())
    .slice(0, 1000);

  const urls = recentes.map(n => `
  <url>
    <loc>${SITE_URL}/noticias/${n.id}</loc>
    <news:news>
      <news:publication>
        <news:name>${SITE_NAME}</news:name>
        <news:language>pt</news:language>
      </news:publication>
      <news:publication_date>${new Date(n.data).toISOString()}</news:publication_date>
      <news:title><![CDATA[${n.titulo}]]></news:title>
      <news:keywords>${n.categoria}, direito, jurídico, brasil</news:keywords>
    </news:news>
  </url>`).join('');

  const xml = `<?xml version="1.0" encoding="UTF-8"?>
<urlset
  xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"
  xmlns:news="http://www.google.com/schemas/sitemap-news/0.9">
${urls}
</urlset>`;

  return new Response(xml, {
    headers: {
      'Content-Type': 'application/xml; charset=utf-8',
      'Cache-Control': 'public, max-age=3600',
    },
  });
};
