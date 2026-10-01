# Predicción antes de medir
*Norma del owner, 2026-10-01 (D4).*

- Antes de cada medida (tiempo de scan sobre SMB, MB/s de hashing, memoria con N ficheros) se escribe la predicción: orden de magnitud y criterio de éxito.
- Una predicción fallida se registra como hallazgo `Hn` en la bitácora, con el comando que la reproduce.
- Las estimaciones de los informes de research (`docs/research/`) son hipótesis hasta que se miden sobre el NAS real.

**Por qué:** el coste de lecturas sobre el servidor es el requisito central del proyecto; sin medir no hay forma de saber si la ventana de inactividad basta.
