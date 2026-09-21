# Auditoria de ordenes CESER

Sistema independiente de TECH para conciliar ordenes entre los XLSX de
**Repuestos** y **Aseguradoras** (Google Drive) y la base **MariaDB de TECH**,
calcular utilidades y margenes, y registrar las situaciones que requieren revision.

## Estado actual: Etapa 4 — automatizado en el servidor, con interfaz de revision

Lo que hay implementado hoy:

- configuracion por variables de entorno (`app/config/settings.py`);
- cliente de Google Drive de solo lectura, por **File ID** (`app/drive/client.py`);
- descarga de la version actual del archivo, con registro de version
  (file id, `modifiedTime`, md5 de Drive, sha256 local, fecha de descarga);
- lectura de **todas** las hojas del XLSX sin conversion de tipos (`app/ingestion/workbook.py`);
- validacion de que Repuestos trae 4 hojas y Aseguradoras 6;
- reporte exploratorio por hoja y por columna (`app/exploration/`);
- exportacion del reporte en JSON, Markdown y CSV;
- normalizacion central de numeros de orden y de valores monetarios;
- adaptadores declarativos por hoja hacia un modelo comun;
- conciliacion de ordenes con la jerarquia directa -> TECH -> no encontrada;
- calculos de utilidad y margen (dos bases, ver abajo);
- seis reglas de situaciones para revisar, cada una testeable por separado;
- exportacion de la conciliacion, los descartes y el resumen.

**Todavia NO implementado a proposito:** conexion a MariaDB (estado de la orden,
valor registrado en TECH y pagos), base de auditoria historica, API y frontend.
El punto de conexion con TECH ya esta aislado en `BuscadorOrdenAsociada`.

## Instalacion

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env    # y completar los File ID y credenciales
```

## Uso

Con Google Drive configurado:

```bash
python -m app.main explorar
```

Con archivos ya descargados (permite validar sin credenciales):

```bash
python -m app.main explorar \
  --repuestos-local data/Repuestos.xlsx \
  --aseguradoras-local data/Aseguradoras.xlsx
```

Conciliacion y margenes:

```bash
python -m app.main conciliar \
  --repuestos-local data/REPUESTOS.xlsx \
  --aseguradoras-local data/ASEGURADORAS.xlsx \
  --detalle 20
```

Salidas en `reportes/`:

| Archivo | Para que sirve |
|---|---|
| `reporte_estructural_<sello>.json` | Reporte completo, insumo de las siguientes etapas |
| `reporte_estructural_<sello>.md` | Version legible para revisar con el area |
| `columnas_<sello>.csv` | Una fila por columna, con una columna vacia `significado_confirmado_por_negocio` para que el negocio anote que representa cada campo |
| `precios_<sello>.csv` | **Reporte general de precios**: una fila por orden con el valor de cada fuente — costo del Excel de repuestos, reconocido por la aseguradora (sin IVA y con IVA), presupuestado en TECH, facturado, pagado, saldo y los margenes en sus dos lecturas |
| `anomalias_resumen_<sello>.md` | **Anomalias en general**: totales del periodo, conteo por tipo de situacion con el valor involucrado, y las mas urgentes |
| `anomalias_detalle_<sello>.csv` | **Anomalias en detalle**: una fila por situacion con su explicacion en texto claro y columnas vacias para el seguimiento manual |
| `descartes_<sello>.csv` | Filas cuyo numero de orden no era valido, con el motivo y la fila del archivo |
| `resumen_ejecucion_<sello>.json` | Resumen de la ejecucion y version de los archivos usados |

Pruebas:

```bash
python -m pytest
```

## Acceso a Google Drive

Se soportan dos modos, elegidos automaticamente segun las variables definidas:

1. `GOOGLE_SERVICE_ACCOUNT_FILE` — service account (recomendado en produccion).
   Los dos archivos deben estar compartidos con el correo de la service account.
2. `GOOGLE_OAUTH_CLIENT_SECRETS_FILE` — OAuth de escritorio para desarrollo local;
   el token se guarda en `GOOGLE_OAUTH_TOKEN_FILE` (por defecto `.secrets/token.json`).

El alcance solicitado es `drive.readonly`: la aplicacion no puede modificar los
archivos originales. Si el archivo esta guardado como Google Sheet en lugar de
XLSX, se exporta a XLSX al vuelo.

## Mapeo de columnas confirmado con el area (18/08/2026)

Estas asignaciones NO son inferencias del sistema: fueron confirmadas tras
revisar el reporte estructural. Viven en `app/ingestion/adapters.py`.

| Hoja | Orden | Dato economico | Nota |
|---|---|---|---|
| HAROLD ORIGINAL | — | — | **Excluida**: hoja de captura previa; su contenido se clasifica luego en las otras dos hojas de HAROLD. Incluirla duplicaria 1.871 ordenes |
| HAROLD H.T | `ORDEN` | costo `VALOR`, reconocido `VALOR REPUESTO ASEGURADORA`, `MANO DE OBRA`, `TOTAL COBRADO` | Se auditan las filas **cuya orden aparece en el archivo de aseguradoras o cuya fecha cae dentro del periodo que ese archivo cubre** (desde 13/09/2026; antes, un corte fijo en la fila 1719, que dejaba fuera 39 ordenes con repuesto cobrado en ago-nov 2025). `TOTAL COBRADO` es el punto de partida del flujo de revision (ver abajo) |
| HAROLD NO EN BASE DATOS | `ORDEN` | idem | Ordenes que no se encontraron en TECH |
| SAMSUNG | `Orden` | costo = columna 16 (sin encabezado) | Encabezados corridos; ese valor ya incluye IVA. Su contraparte **se revisa contra TECH**, no contra el XLSX |
| SUPER WEGA | `ORDEN` | costo `COBRO PROVEE` | Su contraparte **se revisa contra TECH** |
| ACOPIO | `ORDEN` | `VALOR TOTAL` como **transporte** | Productos recibidos que hubo que deshacer: solo se cobro el envio |
| FALABELLA | `ORDEN` | `TOTAL`, repuestos `PARTES` | `PARTES` vacia = la orden no llevo repuestos (confirmado) |
| FLAMINGO | `ORDEN` | `TOTAL`, repuestos `PARTES` | `PARTES` vacia = sin repuestos; verificado con la aritmetica de la hoja. No se usan el segundo bloque (`PARTES__1`, `MO`, ...) ni `VALOR EQUIPO`, vacia en las 123 filas |
| VIDA TRANQUI | `ORDEN` | `TOTAL`, repuestos `PARTES`, `MO`, `TRANSPORTE` | Columna de partes vacia = cero (confirmado). Hasta agosto de 2026 se llamaban `VALOR TOTAL DEL SERVICIO`, `VALOR DE PARTES Y/O PIEZAS`, etc.; el adaptador acepta ambos nombres (valores verificados identicos en las 33 filas comunes) |
| SURA | `ORDEN` | `Total $` | No discrimina repuestos: se resuelve contra TECH |
| MOK | `ORDEN` | `Suma de Total` | No discrimina repuestos: se resuelve contra TECH |

Los costos incluyen IVA (confirmado por el area). **`TOTAL COBRADO` de HAROLD
H.T no lo incluye**: verificado el 25/08/2026 contra los datos, el archivo de
aseguradoras le suma el 19 % y los fletes por encima. Ver la advertencia al
final de la seccion siguiente.

**El año de cada mes se deduce del archivo** (13/09/2026). El archivo de
aseguradoras solo trae el nombre del mes. Hasta agosto se asumia 2026 para todo,
pero la version de septiembre empieza en octubre de 2025 — lo prueba la
numeracion de facturas, estrictamente creciente en el orden de la hoja
(octubre 6865 … diciembre 6955 < enero 7020). El adaptador recorre la hoja,
avanza un año en cada cruce diciembre → enero, y ancla el ultimo mes a su
ocurrencia mas reciente que no quede en el futuro. Reconoce los meses por sus
tres primeras letras, lo que absorbe errores reales como `AGOSO` y `SEPTIEM`.
La ventana en la que se exige contraparte de aseguradora se toma de los meses
que el archivo realmente trae, no de una constante.

## Las dos bases del margen

`margen_esperado` se calcula contra el valor total reconocido, que incluye mano
de obra y transporte. `margen_sobre_repuestos` se calcula contra la porcion que
la aseguradora reconoce por repuestos, comparable directamente con el costo.

Definicion del area: **se revisa toda orden por debajo del 15 % en cualquiera de
las dos bases, sin importar la magnitud ni el signo**. La regla las evalua ambas
y la descripcion dice sobre cual base quedo corta.

## Flujo de revision de HAROLD H.T (definido por el area, 25/08/2026)

Es el recorrido que sigue una orden y el que implementa
`regla_total_cobrado_vs_facturado` en `app/rules/situations.py`:

1. **En la hoja HAROLD H.T** se toman `ORDEN`, `FACT` (numero con el que ya se
   pago), `FECHA DE FACTURA`, `VALOR` (lo que nos cobro el proveedor por el
   repuesto), `VALOR REPUESTO ASEGURADORA` (lo que se le cobro a la aseguradora
   por ese repuesto) y `TOTAL COBRADO` (repuesto + mano de obra).
2. **En el archivo de aseguradoras** se busca la misma `ORDEN` y se toma su
   total: `VALOR TOTAL` o `TOTAL`, segun la hoja. Se compara contra
   `TOTAL COBRADO`:

| Resultado | Lectura | Situacion |
|---|---|---|
| Iguales | Las dos fuentes dicen lo mismo | ninguna |
| El archivo cobra **mas** | Es lo normal: trae IVA y fletes. Se desglosa con las columnas `IVA` y `TRANSPORTE` del propio archivo | ninguna si el desglose cierra; `BILLED_ABOVE_CHARGED` si sobra un residuo |
| El archivo cobra **menos** | Se facturo por debajo de lo cobrado | `BILLED_BELOW_CHARGED`, severidad alta |

El desglose no asume la tasa del 19 %: usa las columnas `IVA` y `TRANSPORTE` de
la hoja de la aseguradora. En FALABELLA (328 filas) y VIDA TRANQUI (33) el IVA
declarado es exactamente el 19 % de diagnostico + mano de obra + partes, y
`TOTAL = SUBTOTAL + TRANSPORTE` se cumple en las 451 filas de las tres hojas que
lo discriminan. SURA y MOK no discriminan: ahi la diferencia se reporta como
`BILLING_NOT_BROKEN_DOWN`, informativa.

### Resultado sobre los datos actuales (25/08/2026)

De las 116 ordenes de HAROLD H.T con contraparte en el archivo de aseguradoras:

| | Ordenes |
|---|---|
| El desglose cierra: la diferencia es exactamente IVA + fletes | 86 |
| Sobra un residuo — 28 de ellas por la tarifa de mano de obra ($92.000 o $92.989 en la hoja contra $92.989 o $97.731 en el archivo) | 28 |
| **Se facturo por debajo de lo cobrado** (1017583 y 1017907, $10.000 menos de repuesto cada una) | 2 |

Otras 57 ordenes de H.T no tienen contraparte: 23 son de diciembre de 2025, que
el archivo de aseguradoras (de 2026) no cubre, y 34 son de 2026 y si deberian
estar. Ninguna orden quedo con el total del archivo por debajo del nominal antes
de descontar IVA y fletes.

### Las dos lecturas del margen

Este flujo dejo a la vista que `TOTAL COBRADO` —y por lo tanto
`VALOR REPUESTO ASEGURADORA`, que es su primer sumando— **viene sin IVA**,
mientras que `VALOR`, el costo del proveedor, si lo incluye. Compararlos de
frente subestima el margen en unos 19 puntos.

Por eso cada base de margen se reporta en **dos lecturas**, una al lado de la
otra, y el area decide con cual trabajar:

| Lectura | Que es | Columnas |
|---|---|---|
| Del archivo, tal cual | El valor reconocido como aparece en la hoja, sin IVA | `margen_del_repuesto`, `margen_del_servicio` |
| Comparable con el costo | Ese mismo valor mas el IVA que el archivo cobro por esa porcion | `margen_del_repuesto_con_iva`, `margen_del_servicio_con_iva` |

El IVA de la porcion de repuestos no se asume: se deduce del patron de cada fila
(`iva_sobre_repuestos` en `app/reconciliation/matching.py`). Verificado sobre las
483 filas de las tres hojas que discriminan IVA, existen exactamente dos
patrones y ninguna excepcion:

- el IVA es el 19 % de toda la base gravable, es decir **los repuestos van
  gravados** — FALABELLA 327, FLAMINGO 107, VIDA TRANQUI 33;
- el IVA es el 19 % de la base **sin** los repuestos, es decir solo se grava la
  mano de obra — FLAMINGO 16, todas de repuestos de alto valor.

La base gravable se deduce del propio archivo como `total - IVA - fletes`. Si una
fila no encaja en ninguno de los dos patrones, la segunda lectura queda vacia:
no se reparte un IVA que no se entiende. Hoy eso ocurre en 1 fila de FALABELLA,
la unica sin base gravable.

**La alerta se dispara sobre la lectura comparable** (decision del area,
13/09/2026). `regla_margen_bajo` mide con IVA y la explicacion menciona tambien
la lectura del archivo como contexto. Con la version de Drive del 13/09/2026 las
ordenes marcadas pasan de 9 a 2 — 1017550 con 6,16 % y 1017621 con 14,57 %, ambas
sobre el valor cobrado por repuestos. Si una hoja no permite reconstruir el IVA,
se evalua la lectura del archivo pero la situacion queda como informativa.

## Despliegue en el servidor (20/09/2026)

Instancia `3.89.227.192` (interna `172.31.38.70`), Amazon Linux 2023, Python 3.14.
El proyecto vive en `/home/ec2-user/auditoria-ceser`; la configuracion en su `.env`
(permisos 600) y las credenciales de la base en `~/.config/auditoria-ceser/base.env`.

| Unidad de systemd | Que hace |
|---|---|
| `auditoria-ceser.timer` | Corre la auditoria **todos los dias a las 6:00 am (hora de Colombia)**: baja los dos Excel de Drive, concilia y guarda la corrida. `Persistent=true`, asi que si el servidor estuvo apagado se recupera al encender |
| `auditoria-ceser.service` | La ejecucion en si. Hoy lleva `--sin-tech`; cuando se conecte TECH se le quita esa opcion |
| `auditoria-ceser-web.service` | La interfaz, con gunicorn en el puerto 8080, solo accesible desde la propia maquina. Se reinicia sola si se cae |
| `httpd` (Apache) | Publica **https://auditoria.ceser.com.co** y redirige el trafico sin cifrar. Dos sitios virtuales: `00-default.conf` conserva el sitio de pruebas que ya existia y `10-auditoria.conf` entrega la interfaz |
| `certbot-renew.timer` | Renueva solo el certificado de Let's Encrypt (emitido el 21/09/2026, vence el 20/12/2026) |

El puerto **80 debe seguir abierto** en el grupo de seguridad: sin el, la renovacion
automatica del certificado falla. El **8080 no debe abrirse nunca**: la interfaz se
alcanza unicamente por HTTPS.

Al acceso por SSH de esta instancia le pasa algo aparte: el servicio corre y escucha
en el puerto 22, el grupo de seguridad y la ACL lo permiten, pero las conexiones no
llegan. Mientras se resuelve, la administracion se hace por **Session Manager**
(consola de AWS, rol `EC2-SessionManager`), que no usa el puerto 22.

Ver que paso: `journalctl -u auditoria-ceser.service -n 50`.
Correr la auditoria a mano: `sudo systemctl start auditoria-ceser.service`.
Cuando se actualice el codigo: copiar los archivos y `sudo systemctl restart auditoria-ceser-web`.

### Usuarios de la interfaz

Viven en `WEB_USUARIOS` del `.env`, como `usuario:hash` separados por `;`. **Nunca se
guarda la contraseña**, solo su hash. Para agregar o cambiar una:

```bash
.venv/bin/python -m app.main clave gerente     # pide la contraseña y devuelve la linea
```

La interfaz solo lee la base de auditoria y escribe los estados de revision; no
puede tocar ni los archivos de Drive ni la base de TECH.

## Base de auditoria (13/09/2026)

Cada `conciliar` guarda la corrida completa en una base propia, que es lo que va
a consultar la interfaz del servidor (`app/database/auditoria.py`). **Nunca se
escribe en TECH**: esa base es de produccion y solo se lee.

Por defecto es un archivo SQLite en `data/auditoria.db`, que en el servidor no
exige montar nada. Con `AUDIT_DB_URL`, o `AUDIT_DB_HOST` y compañia, el mismo
codigo escribe en MariaDB. `--sin-base` omite el guardado.

| Tabla | Contenido |
|---|---|
| `corridas` | Una fila por ejecucion: fecha, origen de los archivos, version exacta de cada uno (fecha de modificacion en Drive y sha256), cobertura del archivo de aseguradoras, si se consulto TECH |
| `ordenes` | Una fila por orden por corrida, con **las mismas metricas del CSV de precios**: las dos salen de una sola definicion (`COLUMNAS_ORDEN`), asi que no pueden desalinearse. Incluye facturas y fechas de ambos lados, margenes en sus dos lecturas, y el veredicto, esperado y residuo del cruce |
| `situaciones` | Lo que presento cada orden en esa corrida, con su severidad y explicacion |
| `revisiones` | El estado que marca quien revisa (pendiente, verificada, en gestion, descartada). **No depende de la corrida**: lo ya revisado sigue revisado en la siguiente |
| `historial_revision` | Cada cambio de estado, con quien, cuando y nota |

`dinero_por_mes()` entrega lo gastado contra lo recibido con la misma metrica de
la vista de revision: gastado, facturado, IVA y fletes cuentan solo las ordenes
con contraparte, y las demas se reportan aparte con su gasto.

La corrida se guarda en una sola transaccion: si algo falla, no queda nada a
medias.

## Cobro tardio del proveedor (definido por el area, 13/09/2026)

Cuando el proveedor nos cobra un repuesto **despues** de que ya se le facturo a
la aseguradora, cobro fuera de tiempo. Siempre que no sea un cobro repetido, es
una anomalia que se muestra en el analisis (`BILLED_BEFORE_PURCHASE`):

- un mes de atraso: severidad media (puede ser desfase de cierre);
- dos meses o mas: severidad alta;
- el mensaje confirma si la orden aparece **una sola vez** en la hoja del
  proveedor. Esa verificacion incluye las filas anteriores al corte de la hoja,
  porque la primera mitad de un cobro doble puede estar en una fila fuera del alcance auditado.

Con la version del 13/09/2026: 56 ordenes (46 con un mes, 10 con dos a cinco),
todas de cobro unico. El caso extremo es la 1017386: facturada a Falabella en
noviembre de 2025 (factura 6911) y cobrada por Harold el 7 de abril de 2026.

## Decisiones de diseno de esta etapa

- **Nada se asume.** Las columnas se marcan como *candidatas* a orden, monetaria,
  cantidad o fecha, con el puntaje y las razones de la sospecha. La confirmacion
  la da el negocio sobre el CSV generado.
- **Sin conversion silenciosa.** Las hojas se leen con `dtype=object`: `010582`,
  `10582.0`, `"10582 "` y `10582` llegan al reporte tal como estan, y el reporte
  cuenta cuantos casos de cada formato existen. Esa evidencia define despues la
  funcion de normalizacion.
- **Vacio no es cero.** Se cuentan por separado nulos, cadenas vacias y ceros.
- **Encabezado no asumido en la fila 1.** Se detecta la fila de encabezado real
  y se reporta cuando no es la primera.
- **Trazabilidad de version.** Cada ejecucion registra con que version exacta de
  cada archivo se trabajo.
- **Las diferencias no fallan la ejecucion.** Un numero de hojas distinto al
  esperado se reporta como hallazgo, no como excepcion: interesa ver la estructura.

## Estructura

```
app/
├── config/settings.py         configuracion por entorno
├── drive/client.py            autenticacion, metadatos y descarga por File ID
├── ingestion/
│   ├── workbook.py            lectura cruda de hojas + deteccion de encabezado
│   └── adapters.py            una especificacion declarativa por hoja
├── normalization/
│   ├── orders.py              funcion central de normalizacion de ordenes
│   └── money.py               montos, formatos y errores de formula
├── exploration/
│   ├── profiling.py           perfil por columna y candidaturas
│   └── report.py              reporte estructural
├── reconciliation/
│   ├── matching.py            jerarquia directa -> TECH -> no encontrada
│   ├── calculations.py        utilidad, margen, saldo
│   └── audit.py               ejecucion completa y exportacion
├── rules/situations.py        reglas de situaciones para revisar
└── main.py                    CLI
tests/                         pytest (80 pruebas)
data/                          XLSX descargados (ignorado por git)
reportes/                      salidas (ignorado por git)
```

La base de auditoria, la API y el frontend se crearan cuando se implementen; no
se dejan carpetas vacias.

## Tablas de TECH en alcance (definido con el area, 19/08/2026)

Base de produccion: `ceser` (MariaDB en la instancia EC2 `ip-172-31-36-175`).

| Concepto | Tabla | Clave de cruce | Nota |
|---|---|---|---|
| Ordenes | `ordenes` | **`PREFIJO_ORDEN`** (PRIMARY) | El numero del Excel es `PREFIJO_ORDEN`, no `ORDEN`: `ORDEN` es solo el consecutivo y se repite entre prefijos |
| Pagos | `abonos` | `ORDEN` (indexada) | Hay numeros reutilizados por esquemas antiguos: se filtra por fecha |
| Ordenes de seguro | `productos_seguro` | **`PREFIJO_CONSECUTIVO_M`** (PRIMARY) | Las ordenes de aseguradora NO estan en `ordenes`. `NRO_SINIESTRO_M` es el "CASO CW" de los Excel |
| Valores de seguro | **`presupuesto_seguro`** | `PREFIJO_CONSECUTIVO_M` | Una fila por item; "Mano de Obra" se separa de los repuestos. Es la fuente que arbitra las diferencias |
| Estado | `ordenes.ESTADO` | | `est_orden` **no tiene indice por ORDEN** (1,1 M de filas, MyISAM): consultarla bloquea la tabla |

Descartadas tras revisarlas: `repuestos` (es el flujo de pedidos al proveedor y
no tiene costo), `control_imp_falabella` (registro de impresiones),
`notas_credito` y `conciliacion` (fuera de alcance por decision del area).

Version del motor: MariaDB 5.5.56, todas las tablas MyISAM.

### Reglas que cruzan las tres fuentes

| Situacion | Que compara |
|---|---|
| Margen menor al minimo | costo del Excel contra el valor reconocido |
| Costo superior al valor reconocido | idem |
| Diferencia entre los dos archivos | hoja de repuestos contra hoja de aseguradora |
| Diferencia entre el archivo y el sistema | hoja de aseguradora contra `presupuesto_seguro` |
| Repuestos aprobados que no se cobraron | el sistema tiene presupuesto aprobado, la orden sigue el flujo normal (`ESTADO_SEGUIMIENTO_M = 4`) y el archivo no cobra repuestos |
| Presupuesto no ejecutado | igual que la anterior pero la orden esta fuera del flujo normal: informativa, suele ser correcta |
| Caso de aseguradora distinto al del sistema | `CASO CW` del archivo contra `NRO_SINIESTRO_M` |
| Orden que no existe en el sistema | la orden esta en los archivos pero no en TECH |
| Saldo pendiente de pago | `VALOR_FAC` contra la suma de `abonos` |
| Orden sin contraparte de aseguradora | orden con repuestos y sin registro de aseguradora |
| Orden facturada sin repuestos registrados | orden con valor reconocido y sin costo |
| Facturada en mas de una hoja | la misma orden aparece en dos hojas |
| Registros de repuestos sin costo | celdas de costo vacias |

### Validacion contra produccion (19/08/2026)

De las 168 ordenes conciliadas, 115 tienen presupuesto registrado en TECH y el
valor de repuestos coincide **exactamente en las 115**. Las 8 ordenes con margen
bajo quedaron confirmadas contra TECH con su numero de siniestro.

## Siguiente paso

MariaDB en modo lectura: estado de la orden, valor registrado en TECH y pagos.
El buscador de orden asociada (`app/reconciliation/matching.py`) ya esta
definido como interfaz y hoy devuelve None.
