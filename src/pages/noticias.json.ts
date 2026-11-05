import type { APIRoute } from 'astro';
import noticias from '../data/noticias.json';

export const GET: APIRoute = () => {
  return new Response(JSON.stringify({ noticias }), {
    headers: {
      'Content-Type': 'application/json',
      'Access-Control-Allow-Origin': '*',
    },
  });
};
