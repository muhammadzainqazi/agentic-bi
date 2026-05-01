import pandas as pd
import os

os.makedirs('data', exist_ok=True)

data = {
    'month': ['2024-01','2024-01','2024-01','2024-02','2024-02','2024-02','2024-03','2024-03','2024-03','2024-04','2024-04','2024-04'],
    'facility': ['Al Rahba Hospital','Cleveland Clinic','Sheikh Khalifa Medical City','Al Rahba Hospital','Cleveland Clinic','Sheikh Khalifa Medical City','Al Rahba Hospital','Cleveland Clinic','Sheikh Khalifa Medical City','Al Rahba Hospital','Cleveland Clinic','Sheikh Khalifa Medical City'],
    'specialty': ['Cardiology','Oncology','Orthopedics','Cardiology','Oncology','Orthopedics','Cardiology','Oncology','Orthopedics','Cardiology','Oncology','Orthopedics'],
    'claims_count': [1200,980,1450,1350,1020,1380,1100,1150,1500,1420,1200,1600],
    'approved_claims': [1050,870,1300,1190,900,1240,960,1030,1250,1080,1440,1300],
    'total_amount_aed': [2400000,3200000,1800000,2700000,3400000,1950000,2200000,3600000,1870000,2850000,3800000,2100000]
}

df = pd.DataFrame(data)
df.to_parquet('data/claims.parquet', index=False)
print(f'Done. {len(df)} rows. Columns: {list(df.columns)}')