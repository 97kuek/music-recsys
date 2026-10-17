import { StudyProvider } from './presentation/StudyProvider';
import { studyService } from './app/bootstrap';
import { createRoot } from 'react-dom/client';
import { App } from './app/App';
import './presentation/style.css';
import './presentation/participant.css';

createRoot(document.getElementById('root')!).render(<StudyProvider service={studyService}><App /></StudyProvider>);
