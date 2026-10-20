# OpenAerialMap Explorer

Plugin independente para **QGIS 4.x / Qt6**, desenvolvido por **Esdras Andrade (IGDEMA/UFAL)**.

O OpenAerialMap Explorer permite pesquisar, pré-visualizar, visualizar e baixar imagens aéreas e orbitais disponibilizadas no catálogo OpenAerialMap.

## Funcionalidades

- Pesquisa por extensão visível do mapa ou extensão de uma camada selecionada.
- Filtros por intervalo temporal, plataforma e resolução espacial.
- Prévia das imagens, consulta de metadados e visualização de suas coberturas.
- Inclusão de imagens como camadas raster no QGIS.
- Download integrado de GeoTIFFs com progresso e cancelamento.
- Downloads múltiplos em fila sequencial, sem sobrescrita acidental.
- Janela flutuante e aviso sobre possíveis limitações geométricas e temporais.

## Instalação

Instale o pacote ZIP de distribuição pelo menu **Complementos → Gerenciar e Instalar Complementos → Instalar a partir de ZIP** no QGIS 4.x. Não utilize o ZIP de código-fonte diretamente como pacote instalável sem conferir sua estrutura.

## Observações técnicas

A cobertura geográfica, a resolução espacial, as condições de licenciamento e as datas de aquisição variam conforme os provedores das imagens. Os dados podem apresentar distorções geométricas, paralaxe, imprecisão posicional e diferenças temporais. O plugin depende de serviços remotos que podem ficar temporariamente indisponíveis.

Este é um projeto independente e não possui afiliação oficial com OpenAerialMap ou Humanitarian OpenStreetMap Team (HOT). As licenças das imagens devem ser verificadas individualmente.

**Licença do código do plugin:** GNU GPL v3. **Versão candidata:** 0.5.5 (experimental).

> O código completo ainda está em processo de publicação neste repositório. 
