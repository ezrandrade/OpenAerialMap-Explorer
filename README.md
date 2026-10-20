# OpenAerialMap Explorer — QGIS 4.x

**Versão candidata 0.5.6 (experimental; ainda não publicada no repositório oficial).**

Plugin comunitário independente desenvolvido por Esdras Andrade; não é um produto oficial do OpenAerialMap ou do Humanitarian OpenStreetMap Team (HOT).

## Objetivo

Pesquisar e explorar imagens disponíveis no catálogo OpenAerialMap diretamente no QGIS 4.x, visualizar mosaicos remotos e baixar GeoTIFFs fornecidos pelos respectivos publicadores.

## Requisitos

- QGIS 4.x, com Qt6/PyQGIS; testes de utilização efetuados pelo desenvolvedor no Windows. Linux, Flatpak e macOS dependem de testes adicionais.
- Conexão HTTPS para os serviços OpenAerialMap/HOT; eventuais indisponibilidades e limites dos serviços externos podem impedir prévias, mosaicos e downloads.
- Nenhum pacote Python adicional declarado no código.

## Instalação

QGIS → Complementos → Gerenciar e Instalar Complementos → Instalar a partir de ZIP → selecione o ZIP. Depois, abra **Raster → OpenAerialMap Explorer** ou o ícone de drone na barra de ferramentas.

## Utilização

1. Abra o complemento e escolha **Mapa-base** para acrescentar o mosaico remoto ao projeto.
2. Em **Explorar**, pesquise na extensão visível ou pela extensão de uma camada selecionada. A pesquisa por camada utiliza a caixa envolvente, não uma máscara por geometria.
3. Utilize filtros temporais, de plataforma e de resolução, conforme a informação disponibilizada no catálogo.
4. Selecione um resultado para consultar metadados e miniatura (quando disponíveis); use **Ir para a imagem** ou **Exibir coberturas**.
5. Utilize **Adicionar imagem** para incorporar a visualização remota ao projeto. A renderização depende do servidor de tiles.
6. Para obter a imagem original, use **Download da Imagem Original**: escolha nome e pasta de destino. Se habilitado, o raster será adicionado ao QGIS após a conclusão.
7. Para múltiplos downloads, selecione vários resultados com Ctrl/Shift e inicie a fila. É realizado um download por vez, com histórico apenas durante a sessão e possibilidade de cancelar.

## Limitações técnicas e científicas

- As imagens são fornecidas por terceiros, com diferentes datas, resoluções, regiões e condições de licença. Consulte os metadados de cada cena e faça a atribuição pertinente.
- O acervo não fornece cobertura contínua, homogênea, nem garantia de atualidade para qualquer lugar.
- A pesquisa atualmente limita os resultados retornados em cada consulta; refine a extensão para áreas grandes.
- O modo de transparência por valores 0 ou 255 pode eliminar pixels válidos pretos ou brancos. Prefira máscaras NoData originais quando disponíveis.
- O download verifica uma assinatura TIFF básica, mas isso não substitui validação completa de integridade, georreferenciamento e leitura pelo GDAL.
- A fila é sequencial e não possui retomada automática após reinicialização do QGIS.
- As URLs de visualização e os downloads dependem da operação dos serviços HOT/OpenAerialMap.

## Segurança, licenciamento e privacidade

O plugin faz solicitações HTTPS ao catálogo e aos serviços de imagem do OpenAerialMap/HOT. O limite geográfico pesquisado e o identificador da imagem selecionada são transmitidos aos servidores remotos. Não há autenticação própria. Consulte a política dos serviços de terceiros antes de trabalhar com dados sensíveis.

**Código-fonte do complemento:** GNU General Public License, versão 3 (GPL-3.0-only), conforme o arquivo `LICENSE`.

**Licenciamento das imagens:** independente da licença do plugin; a utilização de cada imagem está sujeita às condições descritas pelo publicador nos metadados.

## Estado de publicação

Esta versão é candidata a submissão e **não deve ser enviada** ao catálogo oficial antes de configurar links públicos reais para página do plugin, repositório de código e rastreador de problemas no `metadata.txt`, bem como finalizar testes em diferentes sistemas e a revisão de licenças dos recursos incluídos.

Documentação técnica dos serviços: https://docs.imagery.hotosm.org/


## Autoria e limitações dos dados

**Desenvolvedor:** Esdras Andrade (IGDEMA/UFAL).

Na abertura, o complemento informa que imagens de terceiros podem apresentar distorções geométricas, imprecisão posicional, paralaxe e incompatibilidades temporais entre aquisições. É responsabilidade do usuário validar georreferenciamento, resolução, datas, metadados e licenciamento para cada finalidade. O aviso permite marcar «Não mostrar este aviso novamente»; a preferência é mantida nas configurações do QGIS.


## Licença e autoria

Código do plugin: Copyright © 2026 Esdras Andrade. Distribuído conforme o arquivo LICENSE (GNU GPL versão 3). As imagens acessadas pertencem aos respectivos provedores e têm licenças próprias, que devem ser verificadas nos metadados.

## Publicação

A versão candidata está em preparação; os links públicos de homepage, repositório e tracker ainda não foram registrados. Não associar a candidatura a um repositório inexistente.
