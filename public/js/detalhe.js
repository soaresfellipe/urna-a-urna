/* Painel Urna a Urna — detalhe da seção (roteamento por hash #zona=..&secao=..).
   JS clássico em IIFE, sem dependências e sem build.
   Dados somente via fetch dos JSONs emitidos em public/data/:
   - sinalizacoes.json → achados da seção (módulos) com as referências usadas
     (mediana, MAD, tamanho do grupo, z, q);
   - zonas/{zona}.json → números da seção por cargo (votáveis, pleito,
     integridade, exceções aplicadas).
   Estados obrigatórios: carregando (antes do fetch), erro de fetch, seção não
   informada/encontrada e seção sem achados. O link de volta devolve à tabela
   com os filtros recebidos no hash (fmunicipio, fzona, fmodulo). */
(function () {
  'use strict';

  var ROTULOS_MODULO = {
    integridade: 'Integridade',
    divergencia: 'Divergência',
    cruzado: 'Voto cruzado'
  };

  var elementos = {
    conteudo: document.getElementById('conteudo'),
    carregando: document.getElementById('carregando'),
    erro: document.getElementById('erro'),
    naoEncontrada: document.getElementById('nao-encontrada'),
    textoNaoEncontrada: document.getElementById('texto-nao-encontrada'),
    tituloNaoEncontrada: document.getElementById('titulo-nao-encontrada'),
    voltar: document.getElementById('voltar'),
    tituloSecao: document.getElementById('titulo-secao'),
    localSecao: document.getElementById('local-secao'),
    enderecoSecao: document.getElementById('endereco-secao'),
    semAchados: document.getElementById('sem-achados'),
    listaAchados: document.getElementById('lista-achados'),
    excecoesSecao: document.getElementById('excecoes-secao'),
    numerosCargos: document.getElementById('numeros-cargos')
  };

  /* ---------- Carregamento ---------- */

  function buscarJson(caminho) {
    return fetch(caminho).then(function (resposta) {
      if (!resposta.ok) {
        throw new Error('Resposta ' + resposta.status + ' ao buscar ' + caminho);
      }
      return resposta.json();
    });
  }

  function lerHash() {
    var parametros = {};
    window.location.hash.replace(/^#/, '').split('&').forEach(function (par) {
      if (!par) {
        return;
      }
      var chaveValor = par.split('=');
      parametros[chaveValor[0]] = window.decodeURIComponent(chaveValor[1] || '');
    });
    return parametros;
  }

  function numeroValido(valor) {
    return valor !== undefined && valor !== '' && isFinite(Number(valor));
  }

  function mostrar(carregando, conteudo, erro, naoEncontrada) {
    elementos.carregando.hidden = !carregando;
    elementos.conteudo.hidden = !conteudo;
    elementos.erro.hidden = !erro;
    elementos.naoEncontrada.hidden = !naoEncontrada;
  }

  function montarVolta(parametros) {
    var partes = [];
    if (parametros.fmunicipio) { partes.push('municipio=' + parametros.fmunicipio); }
    if (parametros.fzona) { partes.push('zona=' + parametros.fzona); }
    if (parametros.fmodulo) { partes.push('modulo=' + parametros.fmodulo); }
    elementos.voltar.href = partes.length
      ? 'index.html#' + partes.join('&')
      : 'index.html';
  }

  function carregar() {
    mostrar(true, false, false, false);

    var parametros = lerHash();
    if (!numeroValido(parametros.zona) || !numeroValido(parametros.secao)) {
      elementos.tituloNaoEncontrada.textContent = 'Seção não informada';
      elementos.textoNaoEncontrada.textContent =
        'O endereço da página precisa identificar a zona e a seção ' +
        '(por exemplo: #zona=4&secao=593). Use os links da tabela de sinalizações.';
      mostrar(false, false, false, true);
      return;
    }
    montarVolta(parametros);

    var zona = Number(parametros.zona);
    var secaoProcurada = Number(parametros.secao);

    var promessaSinalizacoes = buscarJson('data/sinalizacoes.json');
    var promessaZona = buscarJson('data/zonas/' + zona + '.json');

    promessaSinalizacoes.then(function (dadosSinalizacoes) {
      promessaZona.then(function (dadosZona) {
        var entrada = null;
        (dadosSinalizacoes.sinalizacoes || []).forEach(function (item) {
          if (Number(item.zona) === zona && Number(item.secao) === secaoProcurada) {
            entrada = item;
          }
        });
        var registroZona = null;
        ((dadosZona && dadosZona.secoes) || []).forEach(function (item) {
          if (Number(item.secao) === secaoProcurada) {
            registroZona = item;
          }
        });
        if (!entrada && !registroZona) {
          elementos.tituloNaoEncontrada.textContent = 'Seção não encontrada';
          elementos.textoNaoEncontrada.textContent =
            'A seção ' + secaoProcurada + ' da zona ' + zona + ' não consta nos dados ' +
            'emitidos por esta execução do pipeline.';
          mostrar(false, false, false, true);
          return;
        }
        renderizar(entrada, registroZona, zona, secaoProcurada);
      }, function () {
        // sinalizacoes.json carregou, mas o arquivo da zona não existe: a seção
        // não existe na emissão (não é falha de fetch da página).
        elementos.tituloNaoEncontrada.textContent = 'Seção não encontrada';
        elementos.textoNaoEncontrada.textContent =
          'A seção ' + secaoProcurada + ' da zona ' + zona + ' não consta nos dados ' +
          'emitidos por esta execução do pipeline (zonas/' + zona + '.json ausente).';
        mostrar(false, false, false, true);
      });
    }, function () {
      mostrar(false, false, true, false);
    });
  }

  /* ---------- Formatação ---------- */

  function formatarDecimalComVirgula(valor, casas) {
    return Number(valor).toFixed(casas).replace('.', ',');
  }

  function formatarPercentual(valor) {
    return formatarDecimalComVirgula(valor, 1) + '%';
  }

  function formatarQ(valor) {
    var numero = Number(valor);
    if (!isFinite(numero)) {
      return String(valor);
    }
    if (numero === 0) {
      return '0';
    }
    if (numero >= 0.0001) {
      return formatarDecimalComVirgula(numero, 4);
    }
    var expoente = Math.floor(Math.log(numero) / Math.log(10));
    var mantissa = numero / Math.pow(10, expoente);
    return formatarDecimalComVirgula(mantissa, 1) + 'e' + expoente;
  }

  function rotuloModulo(modulo) {
    return ROTULOS_MODULO[modulo] || modulo;
  }

  function rotuloReferencia(achado) {
    var nivel = achado.nivel_referencia || achado.tipo_referencia;
    if (nivel === 'zona') {
      return 'zona eleitoral';
    }
    if (nivel === 'local') {
      return 'local de votação';
    }
    return nivel || '—';
  }

  /* ---------- Construção de DOM ---------- */

  function criarElemento(tag, classe, texto) {
    var elemento = document.createElement(tag);
    if (classe) {
      elemento.className = classe;
    }
    if (texto !== undefined) {
      elemento.textContent = texto;
    }
    return elemento;
  }

  function criarTh(texto, escopo) {
    var th = document.createElement('th');
    th.scope = escopo || 'col';
    th.textContent = texto;
    return th;
  }

  function criarCelula(texto, escopo) {
    if (escopo === 'th') {
      return criarTh(texto, 'row');
    }
    return criarElemento('td', null, texto);
  }

  function criarTabelaComparacao(cabecalho, linhas) {
    var tabela = document.createElement('table');
    var thead = document.createElement('thead');
    var linhaCabecalho = document.createElement('tr');
    cabecalho.forEach(function (texto) {
      linhaCabecalho.appendChild(criarTh(texto));
    });
    thead.appendChild(linhaCabecalho);
    tabela.appendChild(thead);
    var tbody = document.createElement('tbody');
    linhas.forEach(function (celulas) {
      var linha = document.createElement('tr');
      celulas.forEach(function (celula, indice) {
        linha.appendChild(criarCelula(celula, indice === 0 ? 'th' : 'td'));
      });
      tbody.appendChild(linha);
    });
    tabela.appendChild(tbody);
    return tabela;
  }

  /* ---------- Achados (números da seção ao lado da referência) ---------- */

  function linhasAchadoDivergencia(achado) {
    return [
      ['Percentual dos válidos',
        formatarPercentual(achado.valor_observado) + ' (' + achado.votos_candidato +
          ' votos em ' + achado.votos_validos + ' válidos)',
        'mediana ' + formatarPercentual(achado.mediana)],
      ['Diferença da mediana',
        formatarDecimalComVirgula(achado.diferenca_pp, 1) + ' pp',
        'MAD ' + formatarDecimalComVirgula(achado.mad, 2) + ' pp'],
      ['Grupo de referência',
        '—',
        rotuloReferencia(achado) + ' · ' + achado.tamanho_grupo + ' seções'],
      ['Estatísticas da seção',
        'z robusto ' + formatarDecimalComVirgula(achado.z_robusto, 2) +
          ' · z de tamanho ' + formatarDecimalComVirgula(achado.z_tamanho, 2),
        '—'],
      ['q-valor (FDR, Benjamini–Hochberg)', formatarQ(achado.q), '—']
    ];
  }

  function linhasAchadoCruzado(achado) {
    return [
      ['Diferença presidente − governador',
        formatarDecimalComVirgula(achado.valor_observado, 1) + ' pp (' +
          formatarPercentual(achado.percentual_presidente) + ' − ' +
          formatarPercentual(achado.percentual_governador) + ')',
        'mediana ' + formatarDecimalComVirgula(achado.mediana, 1) + ' pp'],
      ['Diferença da mediana',
        formatarDecimalComVirgula(achado.diferenca_pp, 1) + ' pp',
        'MAD ' + formatarDecimalComVirgula(achado.mad, 2) + ' pp'],
      ['Grupo de referência',
        '—',
        rotuloReferencia(achado) + ' · ' + achado.tamanho_grupo + ' seções'],
      ['Estatísticas da seção',
        'z robusto ' + formatarDecimalComVirgula(achado.z_robusto, 2) +
          ' · z de tamanho ' + formatarDecimalComVirgula(achado.z_tamanho, 2),
        '—'],
      ['q-valor (FDR, Benjamini–Hochberg)', formatarQ(achado.q), '—']
    ];
  }

  function linhasAchadoIntegridade(achado) {
    var linhas = [
      ['Regra', achado.regra, '—'],
      ['Valor observado', String(achado.valor_observado),
        'valor esperado ' + String(achado.valor_esperado)]
    ];
    if (achado.excecao_aplicada) {
      linhas.push(['Exceção legítima aplicada', achado.excecao_aplicada.detalhe, '—']);
    }
    return linhas;
  }

  function detalheAchado(achado) {
    if (achado.modulo === 'cruzado') {
      return 'Par ' + (achado.rotulo || (achado.presidente + ' × ' + achado.governador));
    }
    if (achado.modulo === 'divergencia') {
      return (achado.cargo || '') + (achado.candidato ? ' · ' + achado.candidato : '');
    }
    return achado.cargo ? 'Cargo: ' + achado.cargo : '';
  }

  function criarCartaoAchado(achado) {
    var cartao = criarElemento('article', 'achado');

    var cabecalho = criarElemento('h3', 'cabecalho-achado');
    var rotulo = criarElemento('span', 'modulo modulo-' + achado.modulo,
      rotuloModulo(achado.modulo));
    cabecalho.appendChild(rotulo);
    cabecalho.appendChild(criarElemento('span', 'achado-detalhe', detalheAchado(achado)));
    cartao.appendChild(cabecalho);

    var linhas;
    if (achado.modulo === 'divergencia') {
      linhas = linhasAchadoDivergencia(achado);
    } else if (achado.modulo === 'cruzado') {
      linhas = linhasAchadoCruzado(achado);
    } else {
      linhas = linhasAchadoIntegridade(achado);
    }

    var divTabela = criarElemento('div', 'rolagem');
    divTabela.appendChild(criarTabelaComparacao(
      ['Grandeza', 'Nesta seção', 'Referência usada'],
      linhas
    ));
    cartao.appendChild(divTabela);

    if (achado.frase) {
      cartao.appendChild(criarElemento('p', 'frase-achado', achado.frase));
    }
    return cartao;
  }

  function renderizarAchados(entrada) {
    elementos.listaAchados.textContent = '';
    if (!entrada || !entrada.achados || entrada.achados.length === 0) {
      elementos.semAchados.hidden = false;
      elementos.semAchados.textContent =
        'Nenhuma sinalização para esta seção na execução atual do pipeline. ' +
        'Os números da seção por cargo continuam disponíveis abaixo.';
      return;
    }
    elementos.semAchados.hidden = true;
    entrada.achados.forEach(function (achado) {
      elementos.listaAchados.appendChild(criarCartaoAchado(achado));
    });
  }

  /* ---------- Números da seção por cargo ---------- */

  function renderizarCargo(cargo) {
    var bloco = criarElemento('article', 'cargo');

    bloco.appendChild(criarElemento('h3', null, cargo.cargo));
    bloco.appendChild(criarElemento('p', 'resumo-cargo',
      'Aptos: ' + cargo.aptos + ' · Comparecimento: ' + cargo.comparecimento +
      ' · Abstenções: ' + cargo.abstencoes + ' · Válidos: ' + cargo.validos +
      ' · Brancos: ' + cargo.brancos + ' · Nulos: ' + cargo.nulos));

    var divTabela = criarElemento('div', 'rolagem');
    var tabela = document.createElement('table');
    var caption = criarElemento('caption', null,
      'Votos por votável nesta seção — BU e totalização, com percentual dos válidos.');
    tabela.appendChild(caption);
    var thead = document.createElement('thead');
    var linhaCabecalho = document.createElement('tr');
    ['Votável', 'Nome', 'Votos (BU)', 'Votos (totalização)', '% dos válidos']
      .forEach(function (texto) {
        linhaCabecalho.appendChild(criarTh(texto));
      });
    thead.appendChild(linhaCabecalho);
    tabela.appendChild(thead);
    var tbody = document.createElement('tbody');
    (cargo.votaveis || []).forEach(function (votavel) {
      var linha = document.createElement('tr');
      linha.appendChild(criarCelula(String(votavel.votavel)));
      linha.appendChild(criarCelula(votavel.nome));
      linha.appendChild(criarCelula(String(votavel.votos)));
      linha.appendChild(criarCelula(String(votavel.votos_totalizacao)));
      linha.appendChild(criarCelula(
        votavel.percentual_validos === null
          ? '—'
          : formatarDecimalComVirgula(votavel.percentual_validos, 2) + '%'
      ));
      tbody.appendChild(linha);
    });
    tabela.appendChild(tbody);
    divTabela.appendChild(tabela);
    bloco.appendChild(divTabela);

    var integridade = cargo.integridade || {};
    if ((integridade.achados || []).length > 0) {
      var tituloAchados = criarElemento('h4', null, 'Achados de integridade neste cargo');
      bloco.appendChild(tituloAchados);
      var lista = criarElemento('ul');
      integridade.achados.forEach(function (achado) {
        var texto = 'Regra ' + achado.regra + ': observado ' + achado.valor_observado +
          ' contra esperado ' + achado.valor_esperado + '.';
        if (achado.excecao_aplicada) {
          texto += ' Exceção aplicada: ' + achado.excecao_aplicada.detalhe;
        }
        lista.appendChild(criarElemento('li', null, texto));
      });
      bloco.appendChild(lista);
    }
    return bloco;
  }

  function renderizarExcecoes(registroZona) {
    var excecoes = (registroZona && registroZona.excecoes_aplicadas) || [];
    if (excecoes.length === 0) {
      elementos.excecoesSecao.hidden = true;
      elementos.excecoesSecao.textContent = '';
      return;
    }
    var textos = excecoes.map(function (excecao) {
      return excecao.excecao + ' (regra ' + excecao.regra + '): ' + excecao.detalhe;
    });
    elementos.excecoesSecao.hidden = false;
    elementos.excecoesSecao.textContent =
      'Exceções legítimas aplicadas nesta seção (registradas, nunca silenciosas): ' +
      textos.join(' · ');
  }

  function renderizar(entrada, registroZona, zona, secaoProcurada) {
    var municipio = (entrada && entrada.municipio) ||
      (registroZona && registroZona.municipio) || '';
    var local = (entrada && entrada.local) || (registroZona && registroZona.local) || '';
    var endereco = (entrada && entrada.endereco) ||
      (registroZona && registroZona.endereco) || '';

    elementos.tituloSecao.textContent = 'Zona ' + zona + ' · Seção ' + secaoProcurada +
      (municipio ? ' — ' + municipio : '');
    elementos.localSecao.textContent = local ? 'Local de votação: ' + local : '';
    elementos.enderecoSecao.textContent = endereco || '';

    renderizarAchados(entrada);

    elementos.numerosCargos.textContent = '';
    if (registroZona) {
      renderizarExcecoes(registroZona);
      (registroZona.cargos || []).forEach(function (cargo) {
        elementos.numerosCargos.appendChild(renderizarCargo(cargo));
      });
    } else {
      elementos.excecoesSecao.hidden = true;
      elementos.excecoesSecao.textContent = '';
      elementos.numerosCargos.appendChild(criarElemento('p', 'vazio',
        'Os números por cargo desta seção não constam no arquivo da zona emitido.'));
    }

    mostrar(false, true, false, false);
  }

  carregar();

  // Navegações que mudam só o hash (zona/seção) não recarregam a página:
  // reexecuta o carregamento para refletir o novo endereço.
  window.addEventListener('hashchange', carregar);
})();
