/* Painel Urna a Urna — metodologia: preenche os limiares reais da emissão.
   JS clássico em IIFE, sem dependências e sem build.
   Os valores exibidos vêm dos dados gerados (index.json / sinalizacoes.json);
   nada é fixado na página. Estados obrigatórios: carregando, erro de fetch. */
(function () {
  'use strict';

  var elementos = {
    conteudo: document.getElementById('conteudo'),
    carregando: document.getElementById('carregando'),
    erro: document.getElementById('erro'),
    botaoTentar: document.getElementById('botao-tentar'),
    seloEmissao: document.getElementById('selo-emissao'),
    multiplicadores: document.getElementById('multiplicadores'),
    listaPares: document.getElementById('lista-pares'),
    referenciaCruzado: document.getElementById('referencia-cruzado')
  };

  function buscarJson(caminho) {
    return fetch(caminho).then(function (resposta) {
      if (!resposta.ok) {
        throw new Error('Resposta ' + resposta.status + ' ao buscar ' + caminho);
      }
      return resposta.json();
    });
  }

  function formatarNumero(valor) {
    var numero = Number(valor);
    if (Number.isInteger(numero)) {
      return numero.toLocaleString('pt-BR');
    }
    return numero.toLocaleString('pt-BR', { maximumFractionDigits: 2 });
  }

  function formatarFracionario(valor) {
    var texto = String(Number(valor));
    return texto.replace('.', ',');
  }

  function formatarLimiar(chave, valor) {
    if (chave === 'fdr_alpha') {
      return formatarFracionario(valor);
    }
    return formatarNumero(valor);
  }

  function preencherLimiares(indice) {
    var alvos = document.querySelectorAll('.limiar[data-limiar]');
    Array.prototype.forEach.call(alvos, function (alvo) {
      var partes = alvo.getAttribute('data-limiar').split(':');
      var modulo = partes[0];
      var chave = partes[1];
      var valor = indice.limiares[modulo][chave];
      alvo.textContent = formatarLimiar(chave, valor);
    });
  }

  function preencherMultiplicadores(indice) {
    var anos = Object.keys(indice.multiplicador_votos_por_eleitor);
    anos.sort();
    var itens = [];
    anos.forEach(function (ano) {
      var cargos = indice.multiplicador_votos_por_eleitor[ano];
      Object.keys(cargos).sort().forEach(function (cargo) {
        itens.push(ano + ' (' + cargo + '): ' + formatarNumero(cargos[cargo]));
      });
    });
    if (itens.length) {
      elementos.multiplicadores.textContent = itens.join('; ');
    } else {
      elementos.multiplicadores.textContent = 'nenhum multiplicador diferenciado nesta emissão';
    }
  }

  function preencherPares(indice) {
    var pares = (indice.voto_cruzado || {}).pares || [];
    elementos.listaPares.textContent = '';
    if (!pares.length) {
      var itemVazio = document.createElement('li');
      itemVazio.textContent = 'Nenhum par aprovado nesta emissão.';
      elementos.listaPares.appendChild(itemVazio);
      return;
    }
    pares.forEach(function (par) {
      var item = document.createElement('li');
      var presidente = par.presidente.nome + ' (' + formatarNumero(par.presidente.numero) + ')';
      var governador = par.governador.nome + ' (' + formatarNumero(par.governador.numero) + ')';
      item.textContent = par.rotulo + ' — Presidente: ' + presidente + ' × Governador: ' + governador;
      elementos.listaPares.appendChild(item);
    });
  }

  function tituloNivel(nivel) {
    if (nivel === 'municipio') { return 'município'; }
    if (nivel === 'zona') { return 'zona'; }
    return nivel;
  }

  function preencherReferencia(indice) {
    var nivel = indice.referencia.nivel;
    var fallback = indice.referencia.fallback;
    var texto = 'referência principal: ' + tituloNivel(nivel);
    if (nivel === 'municipio' && fallback === 'zona') {
      texto += ' (fallback: ' + tituloNivel(fallback) + ' — no DF há um único município, '
        + 'então as sinalizações do voto cruzado desta emissão usam a zona)';
    }
    elementos.referenciaCruzado.textContent = texto;
  }

  function preencherTotalSecoes(indice) {
    var alvo = document.getElementById('total-secoes-limitacoes');
    if (alvo) {
      alvo.textContent = formatarNumero(indice.total_secoes || 0);
    }
  }

  function preencherSelo(indice) {
    var data = new Date(indice.data_geracao);
    var dataTexto;
    if (isNaN(data.getTime())) {
      dataTexto = indice.data_geracao;
    } else {
      dataTexto = data.toLocaleString('pt-BR', {
        day: '2-digit', month: '2-digit', year: 'numeric',
        hour: '2-digit', minute: '2-digit'
      });
    }
    elementos.seloEmissao.textContent =
      'Emissão: ' + indice.uf + ', eleições de ' + indice.ano + ', '
      + (indice.turno === 2 ? '2º' : '1º') + ' turno — limiares lidos dos dados gerados em '
      + dataTexto + ' (' + formatarNumero(indice.total_secoes || 0)
      + ' seções analisadas). Os valores exibidos nesta página vêm do arquivo '
      + 'data/index.json, não são fixados na página.';
  }

  /* A tabela e o detalhe chegam aqui levando os filtros ativos no hash
     (municipio, zona, modulo); o link "Tabela" desta página devolve os
     mesmos filtros para que o percurso completo os preserve. */
  function preservarFiltrosNoHash() {
    var hash = window.location.hash;
    if (!hash || hash === '#') {
      return;
    }
    var temFiltro = hash.replace(/^#/, '').split('&').some(function (par) {
      var chave = par.split('=')[0];
      return chave === 'municipio' || chave === 'zona' || chave === 'modulo';
    });
    if (!temFiltro) {
      return;
    }
    Array.prototype.forEach.call(
      document.querySelectorAll('a[href^="index.html"]'),
      function (link) { link.setAttribute('href', 'index.html' + hash); }
    );
  }

  function carregar() {
    preservarFiltrosNoHash();
    elementos.erro.hidden = true;
    elementos.conteudo.hidden = true;
    elementos.carregando.hidden = false;

    buscarJson('data/index.json').then(function (indice) {
      preencherLimiares(indice);
      preencherMultiplicadores(indice);
      preencherPares(indice);
      preencherReferencia(indice);
      preencherTotalSecoes(indice);
      preencherSelo(indice);
      elementos.carregando.hidden = true;
      elementos.conteudo.hidden = false;
    }).catch(function () {
      elementos.carregando.hidden = true;
      elementos.erro.hidden = false;
    });
  }

  if (elementos.botaoTentar) {
    elementos.botaoTentar.addEventListener('click', carregar);
  }

  carregar();
})();
