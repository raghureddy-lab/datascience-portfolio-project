import webscrapping.glassdoor as gs

df = gs.get_jobs("data scientist", 10, verbose=True)

print(df.head())