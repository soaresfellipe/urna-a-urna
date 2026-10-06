/* Painel Urna a Urna — tabela ranqueada, filtros combináveis e exportação CSV.
   JS clássico em IIFE, sem dependências e sem build.
   Dados somente via fetch dos JSONs emitidos em public/data/.
   Estados obrigatórios: carregando (antes do fetch), erro de fetch e vazio.
   A célula da seção liga ao detalhe (secao.html) levando os filtros ativos no
   hash, para que o retorno à tabela os preserve. */
(function () {
  'use strict';

  var ROTULOS_MODULO = {
    integridade: 'Integridade',
    divergencia: 'Divergência',
    cruzado: 'Voto cruzado'
  };

  var CABECALHO_CSV = [
    'Município', 'Zona', 'Seção', 'Local', 'Módulo',
    'Valor observado', 'Valor de referência', 'Frase explicativa'
  ];

  var elementos = {
    conteudo: document.getElementById('conteudo'),
    carregando: document.getElementById('carregando'),
    erro: document.getElementById('erro'),
    corpo: document.getElementById('corpo-tabela'),
    contador: document.getElementById('contador'),
    vazio: document.getElementById('vazio'),
    filtroMunicipio: document.getElementById('filtro-municipio'),
    filtroZona: document.getElementById('filtro-zona'),
    filtroModulo: document.getElementById('filtro-modulo'),
    botaoLimpar: document.getElementById('botao-limpar'),
    botaoCsv: document.getElementById('botao-csv'),
    avisoCsv: document.getElementById('aviso-csv'),
    botaoTentar: document.getElementById('botao-tentar')
  };

  var dados = null;
  var filtros = { municipio: '', zona: '', modulo: '' };

  /* ---------- Carregamento ---------- */

  function buscarJson(caminho) {
    return fetch(caminho).then(function (resposta) {
      if (!resposta.ok) {
        throw new Error('Resposta ' + resposta.status + ' ao buscar ' + caminho);
      }
      return resposta.json();
    });
  }

  function carregar() {
    elementos.erro.hidden = true;
    elementos.conteudo.hidden = true;
    elementos.carregando.hidden = false;

    Promise.all([
      buscarJson('data/index.json'),
      buscarJson('data/sinalizacoes.json')
    ]).then(function (resultados) {
      dados = { indice: resultados[0], sinalizacoes: resultados[1].sinalizacoes };
      montarFiltros();
      lerFiltrosDoHash();
      elementos.carregando.hidden = true;
      elementos.conteudo.hidden = false;
      renderizar();
    }).catch(function () {
      elementos.carregando.hidden = true;
      elementos.erro.hidden = false;
    });
  }

  /* ---------- Filtros ---------- */

  function adicionarOpcao(select, valor, texto) {
    var opcao = document.createElement('option');
    opcao.value = valor;
    opcao.textContent = texto;
    select.appendChild(opcao);
  }

  function montarFiltros() {
    var municipios = {};
    dados.sinalizacoes.forEach(function (secao) {
      municipios[secao.municipio_id] = secao.municipio;
    });

    elementos.filtroMunicipio.textContent = '';
    adicionarOpcao(elementos.filtroMunicipio, '', 'Todos os municípios');
    Object.keys(municipios).sort(function (a, b) {
      return municipios[a].localeCompare(municipios[b], 'pt-BR');
    }).forEach(function (id) {
      adicionarOpcao(elementos.filtroMunicipio, id, municipios[id]);
    });

    var zonas = [];
    (dados.indice.manifesto_arquivos || []).forEach(function (arquivo) {
      var partes = /^zonas\/(\d+)\.json$/.exec(arquivo.caminho);
      if (partes) {
        zonas.push(Number(partes[1]));
      }
    });
    zonas.sort(function (a, b) { return a - b; });

    elementos.filtroZona.textContent = '';
    adicionarOpcao(elementos.filtroZona, '', 'Todas as zonas');
    zonas.forEach(function (zona) {
      adicionarOpcao(elementos.filtroZona, String(zona), 'Zona ' + zona);
    });
  }

  function lerFiltrosDoHash() {
    var partes = window.location.hash.replace(/^#/, '');
    if (!partes) {
      return;
    }
    partes.split('&').forEach(function (par) {
      var chaveValor = par.split('=');
      var chave = chaveValor[0];
      var valor = window.decodeURIComponent(chaveValor[1] || '');
      if (chave === 'municipio') { filtros.municipio = valor; }
      if (chave === 'zona') { filtros.zona = valor; }
      if (chave === 'modulo') { filtros.modulo = valor; }
    });
    elementos.filtroMunicipio.value = filtros.municipio;
    elementos.filtroZona.value = filtros.zona;
    elementos.filtroModulo.value = filtros.modulo;
  }

  function escreverHash() {
    var partes = [];
    if (filtros.municipio) { partes.push('municipio=' + filtros.municipio); }
    if (filtros.zona) { partes.push('zona=' + filtros.zona); }
    if (filtros.modulo) { partes.push('modulo=' + filtros.modulo); }
    var alvo = partes.length ? '#' + partes.join('&') : '#';
    window.history.replaceState(null, '', alvo);
  }

  /* ---------- Formatação ---------- */

  function formatarDecimalComVirgula(valor, casas) {
    return Number(valor).toFixed(casas).replace('.', ',');
  }

  function formatarObservado(achado) {
    if (achado.unidade === 'pp') {
      return formatarDecimalComVirgula(achado.valor_observado, 1) + ' pp';
    }
    return formatarDecimalComVirgula(achado.valor_observado, 1) + '% dos válidos';
  }

  function formatarReferencia(achado) {
    var nivel = achado.nivel_referencia || achado.tipo_referencia;
    var texto = formatarDecimalComVirgula(achado.valor_referencia, 1);
    texto += achado.unidade === 'pp' ? ' pp' : '%';
    var grupos = achado.tamanho_grupo
      ? ' (mediana, ' + nivel + ', ' + achado.tamanho_grupo + ' seções)'
      : ' (mediana, ' + nivel + ')';
    return texto + grupos;
  }

  function rotuloModulo(modulo) {
    return ROTULOS_MODULO[modulo] || modulo;
  }

  /* ---------- Filtragem e renderização ---------- */

  function achadoPassaNoFiltro(achado) {
    if (filtros.modulo && achado.modulo !== filtros.modulo) {
      return false;
    }
    return true;
  }

  function secoesFiltradas() {
    return dados.sinalizacoes.filter(function (secao) {
      if (filtros.municipio && secao.municipio_id !== filtros.municipio) {
        return false;
      }
      if (filtros.zona && String(secao.zona) !== filtros.zona) {
        return false;
      }
      return secao.achados.some(achadoPassaNoFiltro);
    }).map(function (secao) {
      return {
        secao: secao,
        achados: secao.achados.filter(achadoPassaNoFiltro)
      };
    });
  }

  function criarTd(classe) {
    var td = document.createElement('td');
    if (classe) {
      td.className = classe;
    }
    return td;
  }

  function criarTdTexto(texto, classe) {
    var td = criarTd(classe);
    td.textContent = texto;
    return td;
  }

  function criarTdAgrupado(texto, quantidadeLinhas) {
    var td = criarTdTexto(texto);
    if (quantidadeLinhas > 1) {
      td.rowSpan = quantidadeLinhas;
    }
    return td;
  }

  function linkDetalhe(secao) {
    var partes = [
      'zona=' + encodeURIComponent(secao.zona),
      'secao=' + encodeURIComponent(secao.secao)
    ];
    if (filtros.municipio) { partes.push('fmunicipio=' + encodeURIComponent(filtros.municipio)); }
    if (filtros.zona) { partes.push('fzona=' + encodeURIComponent(filtros.zona)); }
    if (filtros.modulo) { partes.push('fmodulo=' + encodeURIComponent(filtros.modulo)); }
    return 'secao.html#' + partes.join('&');
  }

  function criarTdSecao(secao) {
    var td = criarTd();
    td.rowSpan = secao.achados.length;
    var link = document.createElement('a');
    link.className = 'link-secao';
    link.href = linkDetalhe(secao);
    link.textContent = String(secao.secao);
    td.appendChild(link);
    return td;
  }

  function criarLinhaAchado(secao, achado, primeira) {
    var linha = document.createElement('tr');
    var quantidade = secao.achados.length;

    if (primeira) {
      linha.appendChild(criarTdAgrupado(secao.municipio, quantidade));
      linha.appendChild(criarTdAgrupado(String(secao.zona), quantidade));
      linha.appendChild(criarTdSecao(secao));
      linha.appendChild(criarTdAgrupado(secao.local, quantidade));
    }

    var tdModulo = criarTd('col-modulo');
    var rotulo = document.createElement('span');
    rotulo.className = 'modulo modulo-' + achado.modulo;
    rotulo.textContent = rotuloModulo(achado.modulo);
    tdModulo.appendChild(rotulo);
    var detalhe = document.createElement('span');
    detalhe.className = 'detalhe';
    detalhe.textContent = detalheAchado(achado);
    tdModulo.appendChild(detalhe);
    linha.appendChild(tdModulo);

    linha.appendChild(criarTdTexto(formatarObservado(achado), 'col-observado'));
    linha.appendChild(criarTdTexto(formatarReferencia(achado), 'col-referencia'));
    linha.appendChild(criarTdTexto(achado.frase, 'col-frase'));
    return linha;
  }

  function detalheAchado(achado) {
    if (achado.modulo === 'cruzado') {
      return achado.rotulo || (achado.presidente + ' × ' + achado.governador);
    }
    return (achado.cargo || '') + (achado.candidato ? ' · ' + achado.candidato : '');
  }

  function renderizar() {
    var filtradas = secoesFiltradas();
    var quantidadeAchados = filtradas.reduce(function (soma, item) {
      return soma + item.achados.length;
    }, 0);

    elementos.corpo.textContent = '';
    filtradas.forEach(function (item) {
      item.achados.forEach(function (achado, indice) {
        elementos.corpo.appendChild(criarLinhaAchado(item.secao, achado, indice === 0));
      });
    });

    var vazia = filtradas.length === 0;
    var semDados = dados.sinalizacoes.length === 0;
    if (vazia) {
      elementos.vazio.hidden = false;
      if (semDados && !filtros.municipio && !filtros.zona && !filtros.modulo) {
        elementos.vazio.textContent = 'Nenhuma seção foi sinalizada nesta execução do pipeline.';
      } else {
        elementos.vazio.textContent = 'Nenhuma sinalização corresponde aos filtros aplicados. ' +
          'Ajuste o município, a zona ou o módulo para ver resultados.';
      }
    } else {
      elementos.vazio.hidden = true;
    }

    var pluralSecoes = filtradas.length === 1 ? 'seção' : 'seções';
    var pluralAchados = quantidadeAchados === 1 ? 'achado' : 'achados';
    elementos.contador.textContent = filtradas.length + ' ' + pluralSecoes + ' · ' +
      quantidadeAchados + ' ' + pluralAchados;

    atualizarExportacao(filtradas, quantidadeAchados);
    escreverHash();
  }

  /* ---------- Exportação CSV ---------- */

  function escaparCsv(valor) {
    var texto = String(valor);
    if (/[";\n\r]/.test(texto)) {
      return '"' + texto.replace(/"/g, '""') + '"';
    }
    return texto;
  }

  function nomeArquivoCsv(filtradas) {
    var ativos = [];
    if (filtros.municipio) {
      ativos.push('municipio-' + filtros.municipio.toLowerCase()
        .normalize('NFD').replace(/[\u0300-\u036f]/g, ''));
    }
    if (filtros.zona) {
      ativos.push('zona-' + filtros.zona);
    }
    if (filtros.modulo) {
      ativos.push('modulo-' + filtros.modulo);
    }
    return ativos.length ? 'sinalizacoes__' + ativos.join('__') + '.csv' : 'sinalizacoes.csv';
  }

  function linhasCsv(filtradas) {
    var linhas = [CABECALHO_CSV.join(';')];
    filtradas.forEach(function (item) {
      item.achados.forEach(function (achado) {
        linhas.push([
          item.secao.municipio,
          item.secao.zona,
          item.secao.secao,
          item.secao.local,
          rotuloModulo(achado.modulo),
          formatarObservado(achado),
          formatarReferencia(achado),
          achado.frase
        ].map(escaparCsv).join(';'));
      });
    });
    return linhas.join('\r\n') + '\r\n';
  }

  function atualizarExportacao(filtradas, quantidadeAchados) {
    var vazia = filtradas.length === 0 || quantidadeAchados === 0;
    elementos.botaoCsv.disabled = vazia;
    if (vazia) {
      elementos.avisoCsv.hidden = false;
      elementos.avisoCsv.textContent = 'Exportação indisponível: nenhum resultado corresponde ' +
        'aos filtros aplicados. Limpe ou ajuste os filtros para exportar.';
    } else {
      elementos.avisoCsv.hidden = true;
      elementos.avisoCsv.textContent = '';
    }
  }

  function exportarCsv() {
    var filtradas = secoesFiltradas();
    if (filtradas.length === 0) {
      return;
    }
    var conteudo = '\ufeff' + linhasCsv(filtradas);
    var blob = new Blob([conteudo], { type: 'text/csv;charset=utf-8' });
    var url = URL.createObjectURL(blob);
    var link = document.createElement('a');
    link.href = url;
    link.download = nomeArquivoCsv(filtradas);
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
  }

  /* ---------- Eventos ---------- */

  elementos.filtroMunicipio.addEventListener('change', function () {
    filtros.municipio = elementos.filtroMunicipio.value;
    renderizar();
  });
  elementos.filtroZona.addEventListener('change', function () {
    filtros.zona = elementos.filtroZona.value;
    renderizar();
  });
  elementos.filtroModulo.addEventListener('change', function () {
    filtros.modulo = elementos.filtroModulo.value;
    renderizar();
  });
  elementos.botaoLimpar.addEventListener('click', function () {
    filtros = { municipio: '', zona: '', modulo: '' };
    elementos.filtroMunicipio.value = '';
    elementos.filtroZona.value = '';
    elementos.filtroModulo.value = '';
    renderizar();
  });
  elementos.botaoCsv.addEventListener('click', exportarCsv);
  elementos.botaoTentar.addEventListener('click', carregar);

  carregar();
})();
