# Registro de contratos vigentes — conocimiento del proceso

## Contexto
- El maestro de contratos (`maestro-contratos.csv`) estuvo congelado del **2026-05-30** hasta que este agente
  empezó a operar. Todo lo registrado después de esa fecha cierra el "gap".
- Antes solo llegaban los contratos que requerían póliza. Ahora **todo contrato, otrosí o acta** debe
  pasar por el buzón `contratos@periferia-ficticia.com`, con o sin póliza.
- No hay área legal: el agente no revisa cláusulas, solo registra datos y alerta riesgos.

## Esquema del maestro (una fila por contrato)
| Campo | Regla |
|---|---|
| id_contrato | Número tal como aparece en el documento. Si no trae número: `AUTO-<año>-<secuencia>`. |
| cliente / nit_cliente | Contraparte (nunca Periferia, NIT 900123456). NIT sin puntos ni dígito de verificación. |
| pais | `CO` (NIT), `EC` (RUC de 13 dígitos), `PE` (RUC de 11), `PA` (RUC), `HN` (RTN). |
| objeto | Máximo 200 caracteres. |
| valor / moneda | Sin separadores. `0` si el contrato es por demanda. Monedas: COP, USD, PEN, PAB, HNL. |
| fecha_inicio / fecha_fin | `YYYY-MM-DD`. Un plazo en meses se convierte en fecha: inicio + n meses − 1 día. |
| requiere_poliza / tipo_poliza / estado_poliza | Un registro nuevo con póliza nace `pendiente`; sin póliza, `no_aplica`. |
| comercial | Nombre según `comerciales.json`. Un remitente desconocido se registra como "No registrado (correo)". |
| fuente | `buzon` para todo lo que entra por este agente. |

## Clasificación
- **Duplicado (RN1):** mismo `id_contrato` con el mismo valor, fecha de inicio y fecha fin. No se escribe nada.
- **Actualización (RN2):** mismo `id_contrato` con algún dato distinto, mismo NIT + objeto muy similar
  (≥ 0.9), o un **otrosí**. Se modifica la fila existente y se deja historial. Si un documento que no es otrosí
  trae el mismo número con datos distintos, es un **conflicto** y pasa a revisión humana.
- **Nuevo (RN3):** no hay coincidencia.
- **Rechazado (RN4):** sin adjunto de contrato (cotizaciones, preguntas) o sin partes ni objeto identificables.
  Un otrosí de un contrato que no está en el maestro también se rechaza: primero se registra el contrato base.

## Confianza y revisión humana (RN5)
- Todo campo con confianza **< 0.8** va a revisión y no se registra sin confirmación explícita.
- Lo que normalmente queda en revisión: valor indeterminado (contratos marco o por demanda), fecha fin
  calculada a partir de un plazo, valor en letras que no coincide con el valor en números, y cualquier valor
  propuesto por el agente que difiera de lo extraído.
- Una póliza condicional (por ejemplo, "para cada orden de servicio que supere…") se registra como requerida y
  se reporta como advertencia.
- Si un otrosí amplía el plazo de un contrato con pólizas, la póliza vuelve a `pendiente` (hay que ampliar
  su vigencia).

## Alertas
1. Contratos que vencen en ≤ 60 días desde la fecha de referencia.
2. Pólizas pendientes: `requiere_poliza = true` y `estado_poliza ≠ vigente`.
3. Contratos registrados o actualizados desde el corte del 2026-05-30.
Anexo: contratos ya vencidos sin acta de terminación ni otrosí (candidatos a cierre o renovación).

## Archivo (SharePoint simulado)
`out/sharepoint/Contratos/<año_inicio>/<cliente-slug>/<id_contrato>.<ext>`; los otrosíes se archivan junto al
contrato base como `<id_contrato>_otrosi-<mensaje>.<ext>`.
